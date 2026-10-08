from pathlib import Path

from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_file` must copy this binary file rather than press it.

    Binary files can't be templates, so they must be copied to their
    destination as-is. This file contains a reference to an
    undefined value, so pressing it would fail.
    """
    platen.press_file("blob.bin", output_dir / "blob.bin")

    assert_file_was_pressed(
        platen.templates_dir / "blob.bin",
        output_dir / "blob.bin",
    )
