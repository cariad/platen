from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from jinja2.loaders import split_template_path

from .exceptions import TemplateNotPressableError


class Loader(FileSystemLoader):
    """
    Loads templates like `FileSystemLoader`, but names a template that isn't UTF-8, and
    records the path to every template that it's asked for.

    Every template is loaded through here, whether it's pressed or referenced (by
    `extends`, `include`, `import`, etc) while another is rendered.

    Args:
        searchpath: Resolved path to the source templates directory.
        on_load: Called with the path to every template that the loader is asked for,
            just before it's read, or `None` to call nothing.
    """

    def __init__(
        self,
        searchpath: Path,
        on_load: Callable[[Path], object] | None,
    ) -> None:
        super().__init__(searchpath)

        self.loaded: set[Path] = set()
        """
        Path to every template file that the loader has loaded or tried to load, whether
        or not it was found, was a regular file, or was UTF-8.
        """

        self._on_load = on_load

    def get_source(
        self,
        environment: Environment,
        template: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        """
        Load a template's source, record its path in `loaded`, and pass its path to
        `on_load`.

        Args:
            environment: The environment that's loading the template.
            template: Name of the template, relative to the source templates directory.

        Returns:
            The template's source, its file name, and a function that says whether the
            file is unchanged.

        Raises:
            TemplateNotPressableError: When the template isn't UTF-8.
        """
        # This is the file that Jinja reads, from the only directory that it searches.
        # `split_template_path` raises the same `TemplateNotFound` for a name like
        # "../doc.md" as Jinja would.
        path = Path(
            self.searchpath[0],
            *split_template_path(template),
        )

        # Record the path before reading it, so that a template that's missing, isn't a
        # regular file, or isn't UTF-8 is recorded too. Watching for changes needs that,
        # because creating or fixing that template is the change to watch for. It needs
        # to look at the template before it's read too, so that a change made after it
        # was read is seen.
        self.loaded.add(path)

        if self._on_load is not None:
            self._on_load(path)

        try:
            return super().get_source(environment, template)
        except UnicodeDecodeError as error:
            # Jinja reads templates as UTF-8, and its error doesn't name the file.
            reason = f"it isn't valid UTF-8 ({error.reason} at byte {error.start})"
            raise TemplateNotPressableError(path, reason) from error
