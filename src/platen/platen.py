from collections.abc import Mapping
from logging import getLogger
from os import PathLike
from pathlib import Path
from shutil import copymode
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)

from .exceptions import NestedDirectoriesError, TemplateNotInDirectoryError

log = getLogger(__name__)


class Platen:
    """
    Presses structured data into documents.

    Call `.press(path)` to press `values` into a single template and
    write the document to `output_dir`. Results are written at the same
    relative path to `output_dir` as the template is to `templates_dir`.

    Templates must exist within `templates_dir` so that referenced
    templates (`extends`, `include`, `import`, etc) can be resolved
    relative to that directory.

    Args:
        templates_dir: Path to the source templates directory.
        output_dir: Path to the build output directory.
        values: Values to press into the templates.

    Raises:
        NestedDirectoriesError: When `templates_dir` and `output_dir`
            are the same directory or nested within each other.
    """

    def __init__(
        self,
        templates_dir: PathLike[str] | str,
        output_dir: PathLike[str] | str,
        values: Mapping[str, Any],
    ) -> None:
        self._templates_dir = Path(templates_dir).resolve()
        self._output_dir = Path(output_dir).resolve()
        self._values = values

        self._assert_directories_not_nested()

        self._env = Environment(
            autoescape=select_autoescape(),
            keep_trailing_newline=True,
            loader=FileSystemLoader(self._templates_dir),
            lstrip_blocks=True,
            trim_blocks=True,
            undefined=StrictUndefined,
        )

    def _assert_directories_not_nested(self) -> None:
        """
        Asserts that the templates and output directories are not the
        same, and that neither is nested within the other.

        Raises:
            NestedDirectoriesError: When the templates and output
                directories are the same or one is nested within the
                other.
        """
        is_nested = self._templates_dir.is_relative_to(
            self._output_dir,
        ) or self._output_dir.is_relative_to(
            self._templates_dir,
        )

        if is_nested:
            raise NestedDirectoriesError(
                self._templates_dir,
                self._output_dir,
            )

    @property
    def output_dir(self) -> Path:
        """Path to the build output directory."""
        return self._output_dir

    def press(self, template: PathLike[str] | str) -> None:
        """
        Press the values into a single template.

        The result will be written to the same relative path in the
        build output directory as the template is in the source
        templates directory.

        For example, if the source templates directory is `./templates`
        and the build output directory is `./build` then:

        - `press("doc.md")` will read from `./templates/doc.md` and
          write to `./build/doc.md`.
        - `press("foo/doc.md")` will read from `./templates/foo/doc.md`
          and write to `./build/foo/doc.md`.

        Args:
            template: Path to the template to press.

        Raises:
            TemplateNotInDirectoryError: When `template` is not a path
                within the source templates directory.

                Templates must exist within the source templates
                directory so that referenced templates (`extends`,
                `include`, `import`, etc) can be resolved relative to
                that directory.
        """
        template = (self._templates_dir / template).resolve()

        if not template.is_relative_to(self._templates_dir):
            raise TemplateNotInDirectoryError(
                template,
                self._templates_dir,
            )

        rel_path = template.relative_to(self._templates_dir).as_posix()
        log.debug("Pressing %s (%s)", template, rel_path)

        t = self._env.get_template(rel_path)
        body = t.render(self._values)

        build_path = self._output_dir / rel_path
        build_path.parent.mkdir(exist_ok=True, parents=True)

        build_path.write_text(
            body,
            encoding="utf-8",
            newline="\n",
        )

        # Replicate the original file permissions:
        copymode(template, build_path)

        log.info("Pressed %s to %s", rel_path, build_path)

    @property
    def templates_dir(self) -> Path:
        """Path to the source templates directory."""
        return self._templates_dir
