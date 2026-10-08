from pathlib import Path

from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_file` must keep this executable file executable.

    Pressed files must have exactly the same permissions as their
    templates. This test specifically tests the pressing of an
    executable script.
    """
    platen.press_file("script.sh", output_dir / "script.sh")

    assert_file_was_pressed(
        platen.templates_dir / "script.sh",
        output_dir / "script.sh",
    )
