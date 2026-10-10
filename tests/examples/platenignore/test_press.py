from pathlib import Path

from platen import Platen
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press` must not press ignored files.

    Files ignored by `.platenignore` files must not be written to the
    output directory, and nor must the `.platenignore` files
    themselves, because no line in them re-includes them. The ignored
    files here reference an undefined value, so pressing any of them
    would fail.
    """
    platen.press(output_dir)
    assert_output_matches_expect()
