from pathlib import Path
from re import search

from .types import LineEnding, Sniff

SNIFF_LENGTH = 8_000
"""
Number of bytes to sniff to learn about a file.

This must remain at least 8,000. Like Git, we'll assume a file is binary
if it contains a NUL (b"\0") character in the first 8,000 bytes.
"""

_LINE_ENDING_PATTERN = b"|".join(
    e.encode() for e in sorted(LineEnding, key=len, reverse=True)
)
"""
Matches any line ending.

Longer sequences are tried first so that a CRLF is not matched as a CR.
"""


def sniff(path: Path) -> Sniff:
    """
    Sniff the file at `path` to discover whether it is text and, if so,
    which line ending it uses.

    Args:
        path: Path to the file to sniff.

    Returns:
        Facts about the file.
    """
    with path.open("rb") as f:
        head = f.read(SNIFF_LENGTH)

        if b"\0" in head:
            # Like Git, we assume a file is binary if it contains a NUL
            # (b"\0") character in the first 8,000 bytes.
            return Sniff(is_text=False)

        match = search(_LINE_ENDING_PATTERN, head)

        if match is None:
            return Sniff(
                is_text=True,
                line_ending=None,
            )

        line_ending = LineEnding(match.group().decode())

        # A CRLF might straddle the end of the head, so peek at the next
        # byte before concluding that this is a lone CR.
        if (
            line_ending is LineEnding.CR
            and match.end() == len(head)
            and f.read(1) == LineEnding.LF.encode()
        ):
            line_ending = LineEnding.CRLF

    return Sniff(
        is_text=True,
        line_ending=line_ending,
    )
