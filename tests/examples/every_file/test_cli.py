from pathlib import Path

from platen.cli import main
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `platen` on the command line must press every file in a templates directory.

    Every file within the templates directory must be pressed to the same relative path
    in the output directory.
    """
    values = Path(__file__).parent / "values.yaml"
    exit_code = main([str(templates_dir), str(values), str(output_dir)])

    assert exit_code == 0
    assert_output_matches_expect()
