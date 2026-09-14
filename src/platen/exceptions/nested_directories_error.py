from pathlib import Path

from .platen_error import PlatenError


class NestedDirectoriesError(PlatenError, ValueError):
    """
    Raised when the templates and output directories are the same, or
    when one is nested within the other.
    """

    def __init__(
        self,
        templates_dir: Path,
        output_dir: Path,
    ) -> None:
        super().__init__(templates_dir, output_dir)
        self.output_dir = output_dir
        self.templates_dir = templates_dir

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return (
            f"Templates directory '{self.templates_dir}' and output "
            f"directory '{self.output_dir}' cannot be the same or "
            "nested within each other"
        )
