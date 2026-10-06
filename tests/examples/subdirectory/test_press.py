from platen import Platen
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    platen: Platen,
) -> None:
    """
    `Platen.press` must press every file in a subdirectory.

    When a directory is given, every file within it must be pressed to
    the same relative path in the output directory, and files outside
    it must not be pressed. `.platenignore` files above the directory
    must still apply.
    """
    platen.press("sub")
    assert_output_matches_expect()
