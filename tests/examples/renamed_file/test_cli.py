from pathlib import Path

from platen.cli import main
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `platen` on the command line must press a template to an output with a different
    name, and only the named output must be written.

    The template includes another template, which must be resolved relative to the
    template's directory.
    """
    values = Path(__file__).parent / "values.yaml"

    exit_code = main(
        [
            str(templates_dir / "README.template"),
            str(values),
            str(output_dir / "README.md"),
        ]
    )

    assert exit_code == 0

    assert_file_was_pressed(
        templates_dir / "README.template",
        output_dir / "README.md",
    )

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "README.md"]
