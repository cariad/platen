from pathlib import Path

from .platen_error import PlatenError


class DestinationIsProtectedError(PlatenError, ValueError):
    """
    Raised before pressing to a destination that is a file that Platen was asked to
    protect, like the file that the values were read from.

    The destination is the protected file when it's the same path, or another name for
    the same file: a symlink or hard link to it, or a spelling that a case- or
    normalisation-insensitive file system (like macOS's default APFS) treats as the same
    name. Pressing would destroy the file.
    """

    def __init__(
        self,
        protected_path: Path,
        destination_path: Path,
    ) -> None:
        super().__init__(protected_path, destination_path)
        self.destination_path = destination_path
        self.protected_path = protected_path

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return (
            f"Destination '{self.destination_path}' is the same file as "
            f"protected file '{self.protected_path}'"
        )
