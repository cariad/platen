from logging import WARNING

from pytest import LogCaptureFixture, mark

from platen import Platen
from tests.types import AssertOutputMatchesExpect


@mark.parametrize(
    "template",
    [None, "."],
    ids=["omitted", "root"],
)
def test(
    assert_output_matches_expect: AssertOutputMatchesExpect,
    caplog: LogCaptureFixture,
    platen: Platen,
    template: str | None,
) -> None:
    """
    `Platen.press` must press every file in the templates directory.

    When no template is given, or the templates directory itself is
    given, every file within it must be pressed to the same relative
    path in the output directory. Text files must be pressed and binary
    files must be copied, and nothing must be logged as a warning.
    """
    platen.press(template)
    assert_output_matches_expect()
    assert not [r for r in caplog.records if r.levelno >= WARNING]
