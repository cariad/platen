from pathlib import Path
from re import escape
from stat import S_IMODE

from pytest import raises

from platen import Platen, TemplateNotInDirectoryError


def test_output_dir_is_path(
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `output_dir` must be the path to the output directory.

    The directory can be specified as a string, but it must be exposed
    here as a `pathlib.Path`.
    """
    platen = Platen(
        templates_dir,
        output_dir.as_posix(),
        {},
    )

    assert platen.output_dir == output_dir


def test_press_updates_permissions(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must update the pressed file's permissions.

    Pressed files must have exactly the same permissions as their
    templates. Overwriting an existing file doesn't change its
    permissions, so this test specifically tests that re-pressing a
    template after its permissions changed updates the pressed file.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    template = templates_dir / "script.sh"
    template.write_text("echo hello\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        output_dir,
        {},
    )

    pressed = platen.output_dir / "script.sh"

    for mode in (0o755, 0o644, 0o700):
        template.chmod(mode)
        platen.press("script.sh")
        assert S_IMODE(pressed.stat().st_mode) == mode


def test_template_is_within_templates_dir(
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
        output_dir,
        {},
    )

    template = templates_dir.parent / "doc.md"

    expect = (
        f"Template '{template}' is not within the templates directory "
        f"'{platen.templates_dir}'"
    )

    with raises(
        TemplateNotInDirectoryError,
        match=escape(expect),
    ) as ex:
        platen.press(template)

    assert ex.value.template_path == template
    assert ex.value.templates_dir == platen.templates_dir


def test_templates_dir_is_path(
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `templates_dir` must be the path to the templates directory.

    The directory can be specified as a string, but it must be exposed
    here as a `pathlib.Path`.
    """
    platen = Platen(
        templates_dir.as_posix(),
        output_dir,
        {},
    )

    assert platen.templates_dir == templates_dir
