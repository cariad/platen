from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture

from platen import Platen
from tests.types import AssertOutputMatchesExpect


def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    caplog: LogCaptureFixture,
    output_dir: Path,
    platen: Platen,
) -> None:
    """
    `Platen.press_directory` must press every file in the templates directory
    when it's given the templates directory itself.

    Every file within the templates directory must be pressed to the same
    relative path in the destination. Text files must be pressed and binary
    files must be copied. The number of files must be returned, and nothing
    must be logged as a warning.
    """
    assert platen.press_directory(platen.templates_dir, output_dir) == 5
    assert_output_matches_expect()
    assert not [r for r in caplog.records if r.levelno >= WARNING]
