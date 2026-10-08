from pathlib import Path

from platen import Platen
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_directory` must press every file in a subdirectory.

    When a directory is given, every file within it must be pressed to
    the same relative path within the destination, and files outside
    it must not be pressed. `.platenignore` files above the directory
    must still apply.
    """
    platen.press_directory("sub", output_dir / "sub")
    assert_output_matches_expect()
