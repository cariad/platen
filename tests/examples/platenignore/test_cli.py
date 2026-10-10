from pathlib import Path

from platen.cli import main
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `platen` on the command line must not press ignored files when it presses a
    templates directory.

    Files ignored by `.platenignore` files must not be written to the output directory,
    and nor must the `.platenignore` files themselves, because no line in them
    re-includes them. The ignored files here reference an undefined value, so pressing
    any of them would fail.
    """
    values = Path(__file__).parent / "values.yaml"
    exit_code = main([str(templates_dir), str(values), str(output_dir)])

    assert exit_code == 0
    assert_output_matches_expect()
