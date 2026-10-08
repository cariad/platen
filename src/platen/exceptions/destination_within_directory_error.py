from pathlib import Path

from .platen_error import PlatenError


class DestinationWithinDirectoryError(PlatenError, ValueError):
    """
    Raised before pressing a directory to a destination within it.

    A press must never write within the directory that it presses. The next press of
    the same directory would read the output as templates, and this press could
    overwrite files that it ignores.
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
