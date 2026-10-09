from pathlib import Path

from .platen_error import PlatenError


class TemplateNotPressableError(PlatenError, ValueError):
    """
    Raised before pressing a template that can't be read as a template, or copied.

    Only regular files are pressed, because reading a FIFO would block, and reading a
    device might never end. Text templates must be UTF-8, because Jinja reads them as
    UTF-8.
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
