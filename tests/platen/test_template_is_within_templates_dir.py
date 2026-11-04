from pathlib import Path
from re import escape

from pytest import mark, raises

from platen import (
    Platen,
    TemplateNotInDirectoryError,
)


@mark.parametrize(
    "template",
    [
        "../doc.md",
        "foo/../../doc.md",
    ],
)
def test_relative_template_is_within_templates_dir(
    template: str,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    Platen must not press templates outside the templates directory,
    even when a relative path reaches them through `..`.
    """
    platen = Platen(
        templates_dir,
        {},
    )

    with raises(TemplateNotInDirectoryError) as ex:
        platen.press_file(template, output_dir / "doc.md")

    expect = templates_dir.parent / "doc.md"

    assert ex.value.template_path == expect


@mark.parametrize(
    ("method", "template", "expect"),
    [
        # A symlink to a file outside the templates directory.
        ("press_file", "outside.md", "doc.md"),
        # A file within a symlink to a directory outside the templates directory.
        ("press_file", "outside_dir/doc.md", "doc.md"),
        ("press_directory", "outside_dir", "."),
    ],
)
def test_symlinked_template_is_within_templates_dir(
    method: str,
    template: str,
    expect: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    Platen must not press an explicitly named template, or directory of templates,
    outside the templates directory, even when a symlink within the templates directory
    leads to it.
    """
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "outside.md").symlink_to(outside_dir / "doc.md")
    (templates_dir / "outside_dir").symlink_to(outside_dir, target_is_directory=True)

    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory

    with raises(TemplateNotInDirectoryError) as ex:
        press(template, output_dir / "doc.md")

    expect_path = (outside_dir / expect).resolve()

    assert ex.value.template_path == expect_path
    assert not output_dir.exists()


@mark.parametrize(
    "method",
    ["press_file", "press_directory"],
)
def test_template_is_within_templates_dir(
    method: str,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    Platen must not press templates outside the templates directory.

    Only templates within the templates directory can be pressed,
    because referenced templates (`extends`, `include`, `import`, etc)
    are resolved relative to that directory.
    """
    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    template = templates_dir.parent / "doc.md"

    expect = (
        f"Template '{template}' is not within the templates directory "
        f"'{platen.templates_dir}'"
    )

    with raises(
        TemplateNotInDirectoryError,
        match=escape(expect),
    ) as ex:
        press(template, output_dir / "doc.md")

    assert ex.value.template_path == template
    assert ex.value.templates_dir == platen.templates_dir
