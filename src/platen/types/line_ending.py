from enum import StrEnum


class LineEnding(StrEnum):
    """A line ending sequence."""

    CR = "\r"
    CRLF = "\r\n"
    LF = "\n"
