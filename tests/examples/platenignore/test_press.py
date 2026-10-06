from platen import Platen
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    platen: Platen,
) -> None:
    """
    `Platen.press` must neither press nor copy ignored files.

    Files ignored by `.platenignore` files must not be written to the
    output directory, and nor must the `.platenignore` files
    themselves. The ignored files here reference an undefined value,
    so pressing any of them would fail.
    """
    platen.press()
    assert_output_matches_expect()
