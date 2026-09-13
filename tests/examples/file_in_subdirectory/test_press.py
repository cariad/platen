from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    platen: Platen,
) -> None:
    """
    `Platen.press` must press this file to an output subdirectory.

    Files must be pressed within the output directory relative to a
    location that matches the template's relative position within the
    source directory. This test specifically tests the pressing of a
    file in a subdirectory.
    """
    platen.press(platen.templates_dir / "foo" / "file.md")
    assert_file_was_pressed(platen.output_dir / "foo" / "file.md")
