from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    platen: Platen,
) -> None:
    """
    `Platen.press` must keep this executable file executable.

    Pressed files must have exactly the same permissions as their
    templates. This test specifically tests the pressing of an
    executable script.
    """
    platen.press("script.sh")
    assert_file_was_pressed(platen.output_dir / "script.sh")
