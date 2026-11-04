from pathlib import Path

from jinja2 import TemplateNotFound
from pytest import mark, raises

from platen import (
    Platen,
    TemplateNotPressableError,
)


def test_loaded_is_empty_before_pressing(
    tmp_path: Path,
) -> None:
    """
    `loaded` must be empty before anything is pressed, even when the templates
    directory holds templates, because constructing a `Platen` loads none of them.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    expect: frozenset[Path] = frozenset()

    assert platen.loaded == expect


def test_loaded_names_every_template_that_press_loads(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `loaded` must name every template that a press loaded or tried to load: those it
    pressed, extended, imported and included, and a name that a list of templates
    passed over because it's missing, but not a `.platenignore` file, which the walk
    reads rather than Jinja.

    Each path must be the templates directory joined with the template's name, without
    resolving any symlink along it.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "_shared").mkdir(parents=True)

    # Ignore every referenced template, so that it's only loaded through a reference.
    (templates_dir / ".platenignore").write_text("_*\n", encoding="utf-8")

    (templates_dir / "page.md").write_text(
        '{% extends "_layout.md" %}\n'
        '{% import "_macros.md" as macros %}\n'
        "{% block body %}{{ macros.greet() }}{% endblock %}\n",
        encoding="utf-8",
    )

    (templates_dir / "_layout.md").write_text(
        '{% block body %}{% endblock %}\n{% include ["_missing.md", "_footer.md"] %}',
        encoding="utf-8",
    )

    (templates_dir / "_macros.md").write_text(
        "{% macro greet() %}Hello{% endmacro %}",
        encoding="utf-8",
    )

    # Reference the footer through a symlink, which `loaded` mustn't resolve.
    (templates_dir / "_shared" / "footer.md").write_text(", world!\n", encoding="utf-8")
    (templates_dir / "_footer.md").symlink_to(Path("_shared") / "footer.md")

    platen = Platen(
        templates_dir,
        {},
    )

    result = platen.press(output_dir)

    assert result == 1

    result = (output_dir / "page.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"

    names = ("_footer.md", "_layout.md", "_macros.md", "_missing.md", "page.md")
    expect = frozenset(platen.templates_dir / name for name in names)

    assert platen.loaded == expect


@mark.parametrize(
    ("template", "expect", "expect_loaded"),
    [
        ("missing.md", TemplateNotFound, ["missing.md"]),
        # The page includes `_legacy.txt`, which isn't UTF-8, so both must be named.
        ("page.md", TemplateNotPressableError, ["_legacy.txt", "page.md"]),
        ("dir", TemplateNotPressableError, ["dir"]),
    ],
)
def test_loaded_names_templates_that_fail_to_load(
    template: str,
    expect: type[TemplateNotFound | TemplateNotPressableError],
    expect_loaded: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `loaded` must name a template that a press tried to load even when the press raised
    because the template was missing, wasn't UTF-8 or wasn't a regular file, because
    creating or fixing that template is the change that a watch waits for.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "dir").mkdir(parents=True)
    (templates_dir / "_legacy.txt").write_bytes(b"Caf\xe9\n")

    (templates_dir / "page.md").write_text(
        '{% include "_legacy.txt" %}\n',
        encoding="utf-8",
    )

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(expect):
        platen.press_file(template, output_dir / "doc.md")

    expect_paths = frozenset(platen.templates_dir / name for name in expect_loaded)
    assert platen.loaded == expect_paths
    assert not output_dir.exists()


def test_on_load_is_called_just_before_each_template_is_read(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `on_load` must be called with the path to every template that a press loads or
    tries to load, as `loaded` names it, in the order that the press loads them, and
    just before the press reads each one, so that a change made then is pressed.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    # Ignore the footer, so that it's only loaded through the include.
    (templates_dir / ".platenignore").write_text("_*\n", encoding="utf-8")

    (templates_dir / "page.md").write_text(
        '{% include ["_missing.md", "_footer.md"] %}',
        encoding="utf-8",
    )

    footer = templates_dir / "_footer.md"
    footer.write_text("Before\n", encoding="utf-8")

    called: list[Path] = []

    def on_load(path: Path) -> None:
        called.append(path)

        if path.name == footer.name:
            footer.write_text("After\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
        on_load=on_load,
    )

    result = platen.press(output_dir)

    assert result == 1

    result = (output_dir / "page.md").read_text(encoding="utf-8")

    assert result == "After\n"

    names = ("page.md", "_missing.md", "_footer.md")
    expect = [platen.templates_dir / name for name in names]

    assert called == expect
