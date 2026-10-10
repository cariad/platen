from pathlib import Path

from .platen_error import PlatenError


class DestinationIsTemplateError(PlatenError, ValueError):
    """
    Raised before pressing to a destination that is one of the press's templates.

    The destination is the template when it's the same path, or another name for the
    same file: a symlink or hard link to it, or a spelling that a case- or
    normalisation-insensitive file system (like macOS's default APFS) treats as the same
    name. Pressing would destroy the template.

    Only the templates that the press finds are protected: the template that
    `press_file` names, or every template that `press` or `press_directory` finds when
    it walks the templates directory, even outside the directory that it presses. Other
    templates, like those that `.platenignore` files ignore, are not, even when another
    template references them (`extends`, `include`, `import`, etc).
    """

    def __init__(
        self,
        template_path: Path,
        destination_path: Path,
    ) -> None:
        super().__init__(template_path, destination_path)
        self.destination_path = destination_path
        self.template_path = template_path

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return (
            f"Destination '{self.destination_path}' is the same file as "
            f"template '{self.template_path}'"
        )
