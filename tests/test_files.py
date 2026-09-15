from pathlib import Path

from pytest import mark

from platen.files import SNIFF_LENGTH, LineEnding, Sniff, sniff


@mark.parametrize(
    ("body", "expect"),
    [
        (
            b"",
            Sniff(
                is_text=True,
            ),
        ),
        (
            b"Plain text.",
            Sniff(
                is_text=True,
            ),
        ),
        (
            b"Plain text.\n",
            Sniff(
                is_text=True,
                line_ending=LineEnding.LF,
            ),
        ),
        (
            b"Plain text.\r\n",
            Sniff(
                is_text=True,
                line_ending=LineEnding.CRLF,
            ),
        ),
        (
            b"Plain text.\r",
            Sniff(
                is_text=True,
                line_ending=LineEnding.CR,
            ),
        ),
        (
            b"One\nTwo\r\n",
            Sniff(
                is_text=True,
                line_ending=LineEnding.LF,
            ),
        ),
        (
            b"Caf\xe9\n",
            Sniff(
                is_text=True,
                line_ending=LineEnding.LF,
            ),
        ),
        (
            b"BIN\x00\n",
            Sniff(
                is_text=False,
            ),
        ),
        (
            b"x\n" + b"x" * SNIFF_LENGTH + b"\x00",
            Sniff(
                is_text=True,
                line_ending=LineEnding.LF,
            ),
        ),
        (
            b"x" * SNIFF_LENGTH + b"\n",
            Sniff(
                is_text=True,
            ),
        ),
        (
            b"x" * (SNIFF_LENGTH - 1) + b"\r\n",
            Sniff(
                is_text=True,
                line_ending=LineEnding.CRLF,
            ),
        ),
        (
            b"x" * (SNIFF_LENGTH - 1) + b"\rx",
            Sniff(
                is_text=True,
                line_ending=LineEnding.CR,
            ),
        ),
    ],
    ids=[
        "empty",
        "no line ending",
        "LF",
        "CRLF",
        "CR",
        "first line ending wins",
        "not UTF-8",
        "binary",
        "NUL after sniffed head",
        "line ending after sniffed head",
        "CRLF straddling sniffed head",
        "CR at end of sniffed head",
    ],
)
def test_sniff(body: bytes, expect: Sniff, tmp_path: Path) -> None:
    """
    Assert that `sniff` distinguishes text from binary and reports the
    first line ending found in the sniffed head.
    """
    path = tmp_path / "file"
    path.write_bytes(body)
    assert sniff(path) == expect
