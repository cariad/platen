from pathlib import Path

from .platen_error import PlatenError


class TemplateNotInDirectoryError(PlatenError, ValueError):
    """
    Raised before pressing a template outside the templates directory.

    Only templates within the templates directory can be pressed,
    because referenced templates (`extends`, `include`, `import`, etc)
    are resolved relative to that directory.
    """

    def __init__(
        self,
        template_path: Path,
        templates_dir: Path,
    ) -> None:
        super().__init__(template_path, templates_dir)
        self.template_path = template_path
        self.templates_dir = templates_dir

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return (
            f"Template '{self.template_path}' is not within the "
            f"templates directory '{self.templates_dir}'"
        )
