from dataclasses import dataclass

from .line_ending import LineEnding


@dataclass(frozen=True)
class Sniff:
    """
    Sniffed facts about a file.

    Attributes:
        is_text: `True` if the file is text. `False` if the file is
            binary.
        line_ending: The first detected line ending. `None` if the file
            is binary. `None` if no line endings were detected within
            the sniff's range.
    """

    is_text: bool
    """`True` if the file is text. `False` if the file is binary."""

    line_ending: LineEnding | None = None
    """
    The first detected line ending. `None` if the file is binary. `None`
    if no line endings were detected within the sniff's range.
    """

    def __post_init__(self) -> None:
        """
        Validate the sniff.

        Raises:
            ValueError: When the sniff is invalid.
        """
        if not self.is_text and self.line_ending is not None:
            raise ValueError("Binary files cannot have a line ending")
