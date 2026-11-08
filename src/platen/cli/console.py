import sys

PROG = "platen"
"""The command's name, which starts every line that it prints."""


def error(message: str) -> None:
    """
    Print a line that says the command failed, and why.

    Args:
        message: What went wrong, on one line.
    """
    print(f"{PROG}: error: {message}", file=sys.stderr)


def say(message: str) -> None:
    """
    Print a line that says what the command is doing.

    Args:
        message: What it's doing, on one line.
    """
    print(f"{PROG}: {message}", file=sys.stderr)


def warn(message: str) -> None:
    """
    Print a line that warns about something that the command carried on from.

    Args:
        message: The warning, on one line.
    """
    print(f"{PROG}: warning: {message}", file=sys.stderr)
