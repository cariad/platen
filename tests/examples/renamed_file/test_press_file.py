from pathlib import Path

from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_file` must press a template to a destination with a different name.

    The template includes another template, which must be resolved relative to the
    templates directory, and only the named destination must be written.
    """
    platen.press_file("README.template", output_dir / "README.md")

    assert_file_was_pressed(
        platen.templates_dir / "README.template",
        output_dir / "README.md",
    )

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "README.md"]
