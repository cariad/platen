from collections.abc import Mapping
from logging import getLogger
from os import PathLike
from pathlib import Path
from shutil import copyfile, copymode
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)
from mosey import Mosey

from .exceptions import NestedDirectoriesError, TemplateNotInDirectoryError
from .files import sniff

IGNORE_FILENAME = ".platenignore"
"""
Name of the files that list patterns of paths to ignore.

See https://cariad.github.io/mosey/ignore-files/ for the rules.
"""

log = getLogger(__name__)


class Platen:
    """
    Presses structured data into documents.

    Call `.press()` to press `values` into every template within `templates_dir` and
    write the documents to `output_dir`, or `.press(path)` to press a single template or
    a directory of templates.

    Results are written at the same relative path to `output_dir` as the template is to
    `templates_dir`.

    Text files are pressed as templates, and binary files are copied as-is.

    When walking a directory, paths matched by `.platenignore` files are neither pressed
    nor copied. `.platenignore` files use `.gitignore`-style patterns: see Mosey's
    documentation for exact rules: https://cariad.github.io/mosey/ignore-files/

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

        self._mosey = Mosey(ignore_filename=IGNORE_FILENAME)

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

    def press(
        self,
        template: PathLike[str] | str | None = None,
    ) -> None:
        """
        Press the values into a template, or into every template within a directory.

        Text files are pressed as templates and binary files are copied as-is.

        When walking a directory, paths matched by `.platenignore` files are neither
        pressed nor copied. `.platenignore` files use `.gitignore`-style patterns: see
        Mosey's documentation for exact rules:
        https://cariad.github.io/mosey/ignore-files/

        A template that's named explicitly is always pressed, because `.platenignore`
        files never apply to explicitly named templates.

        Results are written to the same relative path in the build output directory as
        the template is in the source templates directory.

        For example, if the source templates directory is `./templates`
        and the build output directory is `./build` then:

        - `press()` will press every file within `./templates` to `./build`.
        - `press("doc.md")` will read from `./templates/doc.md` and
          write to `./build/doc.md`.
        - `press("foo")` will press every file within `./templates/foo` to
          `./build/foo`.
        - `press("foo/doc.md")` will read from `./templates/foo/doc.md`
          and write to `./build/foo/doc.md`.

        Args:
            template: Path to the template, or directory of templates, to press.
            Defaults to the entire source templates directory.

        Raises:
            IsADirectoryError: When a directory being pressed holds a symlink to a
                directory. When pressing a directory, symlinks are pressed like files,
                and are never followed into directories.
            TemplateNotInDirectoryError: When `template` is not a path
                within the source templates directory.

                Templates must exist within the source templates
                directory so that referenced templates (`extends`,
                `include`, `import`, etc) can be resolved relative to
                that directory.
        """
        path = (
            self._templates_dir
            if template is None
            else (self._templates_dir / template).resolve()
        )

        if not path.is_relative_to(self._templates_dir):
            raise TemplateNotInDirectoryError(
                path,
                self._templates_dir,
            )

        rel_path = path.relative_to(self._templates_dir).as_posix()

        if not path.is_dir():
            self._press_file(rel_path)
            return

        # Mosey never reads ignore-files above the directory it walks, so walk the whole
        # templates directory and keep only the files within the requested directory.
        # That way, the `.platenignore` files above the requested directory still apply.
        prefix = "" if rel_path == "." else rel_path + "/"
        pressed_any = False

        for step in self._mosey.walk(self._templates_dir):
            if step.name == IGNORE_FILENAME:
                continue

            if step.relative_as_posix.startswith(prefix):
                self._press_file(step.relative_as_posix)
                pressed_any = True

        if not pressed_any:
            log.warning(
                "Nothing to press in %s: it's empty, or everything in it is ignored",
                path,
            )

    def _press_file(self, rel_path: str) -> None:
        """
        Press the values into a single template.

        Text files are pressed as templates. Binary files are copied as-is.

        Args:
            rel_path: POSIX-style path to the template, relative to the source templates
            directory.
        """
        template = self._templates_dir / rel_path
        is_text = sniff(template).is_text

        build_path = self._output_dir / rel_path
        build_path.parent.mkdir(exist_ok=True, parents=True)

        if is_text:
            log.debug("Pressing %s", template)
            t = self._env.get_template(rel_path)
            body = t.render(self._values)

            build_path.write_text(
                body,
                encoding="utf-8",
                newline="\n",
            )

            log.info("Pressed %s to %s", rel_path, build_path)
        else:
            log.debug("Copying binary file %s", template)
            copyfile(template, build_path)
            log.info("Copied %s to %s", rel_path, build_path)

        # Replicate the original file permissions:
        copymode(template, build_path)

    @property
    def templates_dir(self) -> Path:
        """Path to the source templates directory."""
        return self._templates_dir
