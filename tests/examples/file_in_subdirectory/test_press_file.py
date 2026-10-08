from pathlib import Path

from platen import Platen
from tests.types import AssertFileWasPressed


def test(
    assert_file_was_pressed: AssertFileWasPressed,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_file` must press this file in a templates subdirectory,
    named by its absolute path, to the given destination.
    """
    platen.press_file(
        platen.templates_dir / "foo" / "file.md",
        output_dir / "foo" / "file.md",
    )

    assert_file_was_pressed(
        platen.templates_dir / "foo" / "file.md",
        output_dir / "foo" / "file.md",
    )
