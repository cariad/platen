from pathlib import Path

from .platen_error import PlatenError


class TemplateNotPressableError(PlatenError, ValueError):
    """
    Raised when a template can't be read as a template.

    Only regular files are templates, so a directory, a FIFO or a device can't be one.
    Templates must be UTF-8, because Jinja reads them as UTF-8.
    """

    def __init__(
        self,
        template_path: Path,
        reason: str,
    ) -> None:
        super().__init__(template_path, reason)
        self.reason = reason
        self.template_path = template_path

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return f"Template '{self.template_path}' can't be pressed: {self.reason}"
