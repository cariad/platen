from pytest import mark, raises

from platen.types import LineEnding, Sniff


@mark.parametrize(
    "line_ending",
    list(LineEnding),
    ids=[line_ending.name for line_ending in LineEnding],
)
def test_sniff_rejects_line_ending_for_binary(
    line_ending: LineEnding,
) -> None:
    """Assert that a binary `Sniff` cannot be given a line ending."""
    with raises(
        ValueError,
        match="Binary files cannot have a line ending",
    ):
        Sniff(
            is_text=False,
            line_ending=line_ending,
        )
