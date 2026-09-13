from pathlib import Path
from re import escape

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
