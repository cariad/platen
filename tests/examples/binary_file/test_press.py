from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    platen: Platen,
) -> None:
    """
    `Platen.press` must copy this binary file rather than press it.

    Binary files can't be templates, so they must be copied to the
    output directory as-is. This file contains a reference to an
    undefined value, so pressing it would fail.
    """
    platen.press("blob.bin")
    assert_file_was_pressed(platen.output_dir / "blob.bin")
