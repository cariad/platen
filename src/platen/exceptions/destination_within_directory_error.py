from pathlib import Path

from .platen_error import PlatenError


class DestinationWithinDirectoryError(PlatenError, ValueError):
    """
    Raised before pressing a directory to a destination within it.

    The destination is checked once, before anything is pressed. Pressing into the
    directory that's pressed would make the next press read the output as templates,
    and could overwrite files that the press ignores.
    """

    def __init__(
        self,
        directory: Path,
        destination_path: Path,
    ) -> None:
        super().__init__(directory, destination_path)
        self.destination_path = destination_path
        self.directory = directory

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return (
            f"Destination '{self.destination_path}' is within "
            f"'{self.directory}', the directory being pressed"
        )
