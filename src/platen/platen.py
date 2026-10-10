from collections.abc import Callable, Iterable, Mapping
from errno import ENOTDIR
from functools import cache
from logging import getLogger
from os import PathLike, stat, strerror
from os.path import dirname, exists, realpath
from pathlib import Path
from shutil import copymode
from stat import S_ISDIR
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Template,
    TemplateNotFound,
    select_autoescape,
)
from jinja2.loaders import split_template_path
from mosey import Mosey

from .exceptions import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from .files import identity, is_within

IGNORE_FILENAME = ".platenignore"
"""
Name of the files that list patterns of paths to ignore.

See https://cariad.github.io/mosey/ignore-files/ for the rules.
"""

log = getLogger(__name__)


class _Loader(FileSystemLoader):
    """
    Loads templates like `FileSystemLoader`, but names a template that isn't UTF-8.

    Every template is loaded through here, whether it's pressed or referenced (by
    `extends`, `include`, `import`, etc) while another is rendered.
    """

    def get_source(
        self,
        environment: Environment,
        template: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        """
        Load a template's source.

        Args:
            environment: The environment that's loading the template.
            template: Name of the template, relative to the source templates directory.

        Returns:
            The template's source, its file name, and a function that says whether the
            file is unchanged.

        Raises:
            TemplateNotPressableError: When the template isn't UTF-8.
        """
        try:
            return super().get_source(environment, template)
        except UnicodeDecodeError as error:
            # Jinja reads templates as UTF-8, and its error doesn't name the file. Name
            # the file that Jinja read, from the only directory that it searches.
            path = Path(self.searchpath[0], *split_template_path(template))
            reason = f"it isn't valid UTF-8 ({error.reason} at byte {error.start})"
            raise TemplateNotPressableError(path, reason) from error


def _identities(paths: Iterable[Path]) -> dict[tuple[int, int], Path]:
    """
    Identify the files at paths.

    Args:
        paths: Paths to the files to identify.

    Returns:
        Each path that a file exists at, keyed by the file's identity (see `identity`).
        Where paths share an identity, the first is kept.
    """
    identities: dict[tuple[int, int], Path] = {}

    for path in paths:
        # A file that doesn't exist can't be overwritten.
        if (found := identity(path)) is not None:
            identities.setdefault(
                found,
                path,
            )

    return identities


class Platen:
    """
    Presses structured data into documents.

    Call `.press(destination)` to press `values` into every template within
    `templates_dir` and write the documents to `destination`.

    Call `.press_file(template, destination)` to press a single template, or
    `.press_directory(directory, destination)` to press every template within a
    subdirectory.

    `templates_dir` is the root of a tree of templates:

    - Jinja resolves referenced templates (`extends`, `include`, `import`, etc) relative
      to it.
    - Pressing a directory walks it from `templates_dir` down, so that the
      `.platenignore` files above that directory still apply.
    - Every template that's named explicitly must be within it.

    Every file is pressed as a template, so every template must be UTF-8. Walks skip
    common binary files by their names, like images and archives, unless a
    `.platenignore` file re-includes them: see Mosey's documentation for the list:
    https://cariad.github.io/mosey/binary-files/

    When walking a directory, paths matched by `.platenignore` files aren't pressed, and
    nor are the `.platenignore` files themselves, unless one re-includes them.
    `.platenignore` files use `.gitignore`-style patterns: see Mosey's documentation for
    exact rules: https://cariad.github.io/mosey/ignore-files/

    Templates are pressed one at a time, and a press stops at the first error. The
    templates pressed before it stay pressed.

    A relative destination is found from the working directory as each template is
    written, so don't change the working directory during a press, like from a callable
    in `values`.

    Args:
        templates_dir: Path to the source templates directory.
        values: Values to press into the templates.
        protect: Paths to files that must never be pressed over, like the file that the
            values were read from.

            Relative paths are relative to the working directory that Platen is
            constructed in.
    """

    def __init__(
        self,
        templates_dir: PathLike[str] | str,
        values: Mapping[str, Any],
        *,
        protect: Iterable[PathLike[str] | str] = (),
    ) -> None:
        self._templates_dir = Path(realpath(templates_dir))
        self._values = values
        # Anchor relative paths now, like the templates directory, so that changing the
        # working directory before a press can't change which files are protected. They
        # aren't resolved until each press, so that they're checked where they lead.
        self._protect = tuple(Path(p).absolute() for p in protect)

        self._env = Environment(
            autoescape=select_autoescape(),
            keep_trailing_newline=True,
            loader=_Loader(self._templates_dir),
            lstrip_blocks=True,
            trim_blocks=True,
            undefined=StrictUndefined,
        )

        # Walks leave out the `.platenignore` files themselves, and common binary files
        # by their names, unless a `.platenignore` file re-includes them. A walker never
        # changes, so build it once.
        builder = Mosey()
        builder.set_ignore_filename(IGNORE_FILENAME)
        builder.ignore_binary_files()
        self._walker = builder.build()

    def _name(self, named: PathLike[str] | str) -> str:
        """
        Name a template, or directory of templates, the way Jinja names it.

        The path is named by where it leads, through any symlinks. A directory must be,
        because walks never step into symlinks.

        Args:
            named: Path to the template or directory, either relative to the source
                templates directory or absolute.

        Returns:
            POSIX-style path to the template or directory, relative to the source
            templates directory. The directory itself is ".".

        Raises:
            TemplateNotInDirectoryError: When `named` leads outside the source templates
                directory.
        """
        # `Path.resolve()` raises `RuntimeError` for a loop of symlinks on Python 3.11
        # and 3.12, but `realpath` never raises, so a loop fails the same way on every
        # version: with `OSError` when it's identified.
        path = Path(realpath(self._templates_dir / named))

        if path.is_relative_to(self._templates_dir):
            return path.relative_to(self._templates_dir).as_posix()

        # `realpath` keeps the caller's spelling, which a case- or normalisation-
        # insensitive file system can store differently, so find the templates directory
        # by identity before refusing the path. Nothing can be within a templates
        # directory that doesn't exist.
        if (found := identity(self._templates_dir)) is not None:
            for parent in (path, *path.parents):
                if identity(parent) == found:
                    return path.relative_to(parent).as_posix()

        raise TemplateNotInDirectoryError(path, self._templates_dir)

    def _press(
        self,
        jobs: list[tuple[str, Path]],
        templates: list[str],
    ) -> None:
        """
        Press templates one at a time, in order, stopping at the first error.

        Every template that was pressed before the error stays pressed.

        Args:
            jobs: Each template's name within the source templates directory, and the
                path to press it to.
            templates: Names of the templates within the source templates directory
                that no destination may be, including every template in `jobs`.

        Raises:
            DestinationIsProtectedError: When a destination is the same file as a
                protected file.
            DestinationIsTemplateError: When a destination is the same file as any of
                `templates`.
        """
        # Identify every template before anything is written, so that no destination
        # can overwrite one that's yet to be pressed, or one that a later template
        # references. Files are compared by identity rather than by path, so that
        # symlinks, hard links and case-insensitive file systems can't hide them.
        guarded = _identities(self._templates_dir / name for name in templates)
        protected = _identities(self._protect)

        for name, destination in jobs:
            found = identity(destination)

            if found in guarded:
                raise DestinationIsTemplateError(
                    guarded[found],
                    destination,
                )

            if found in protected:
                raise DestinationIsProtectedError(protected[found], destination)

            log.debug("Pressing %s", name)
            body = self._template(name).render(self._values)
            destination.parent.mkdir(exist_ok=True, parents=True)

            destination.write_text(
                body,
                encoding="utf-8",
            )

            # Replicate the template's permissions, but only on a regular file. A
            # destination like `/dev/null` or a terminal must keep its own permissions.
            if destination.is_file():
                copymode(self._templates_dir / name, destination)

            log.info("Pressed %s to %s", name, destination)

    def _template(self, name: str) -> Template:
        """
        Load a template to press.

        Args:
            name: POSIX-style path to the template, relative to the source templates
                directory.

        Returns:
            The template.

        Raises:
            TemplateNotPressableError: When the template isn't a regular file, or isn't
                UTF-8.
            jinja2.TemplateNotFound: When Jinja can't find a file at the template's
                path, like when nothing's there, permissions hide it, or symlinks loop.
        """
        try:
            return self._env.get_template(name)
        except TemplateNotFound:
            # Jinja only opens regular files, so it never blocks on a FIFO or reads a
            # device forever. But it says that anything else isn't found, even a
            # directory that's plainly there. Only the template being pressed is named
            # here: a referenced template is left to Jinja, so that `ignore missing` and
            # lists of templates still skip one that isn't a regular file.
            #
            # `Path.exists()` raises for a path that permissions hide on Python 3.11 to
            # 3.13, but `exists` never raises.
            path = self._templates_dir / name

            if not exists(path):
                raise

            raise TemplateNotPressableError(path, "it isn't a regular file") from None

    def press(self, destination: PathLike[str] | str) -> int:
        """
        Press the values into every template within the source templates directory.

        This is the same as `press_directory(".", destination)`: see `press_directory`
        for how templates are pressed, and the exceptions it raises.

        For example, if the source templates directory is `./templates` then
        `press("build")` will press every file within `./templates` to `./build`.

        Args:
            destination: Path to the directory to press into. Either relative to the
                working directory, or absolute.

        Returns:
            The number of templates pressed, which is 0 when there was nothing to press.
        """
        return self.press_directory(".", destination)

    def press_directory(
        self,
        directory: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> int:
        """
        Press the values into every template within a directory.

        Every template within `directory` is pressed into the directory at
        `destination`, at the same path relative to `destination` as the template is to
        `directory`. Existing files are overwritten, missing directories are created,
        and nothing is ever deleted. Every regular file that's written gets its
        template's permissions, but a destination like `/dev/null` keeps its own.

        Paths matched by `.platenignore` files aren't pressed, and nor are the
        `.platenignore` files themselves or common binary files, like images and
        archives, unless a `.platenignore` file re-includes them. See Mosey's
        documentation for the exact rules: https://cariad.github.io/mosey/ignore-files/
        and https://cariad.github.io/mosey/binary-files/

        Pressing a directory walks the whole source templates directory, so that the
        `.platenignore` files above the directory still apply. Keep templates in a
        dedicated directory, rather than, say, the root of a project, so that walks
        stay quick.

        A directory named through a symlink is pressed as the directory that it leads
        to. Symlinks within the directory are pressed like files, to their own paths
        within the destination, and never walked into.

        The whole directory is walked before anything is pressed. Then the templates are
        pressed one at a time, in walk order, and the press stops at the first error.
        The templates pressed before it stay pressed.

        For example, if the source templates directory is `./templates` then
        `press_directory("foo", "build/foo")` will press every file within
        `./templates/foo` to `./build/foo`.

        Args:
            directory: Path to the directory of templates to press. Either relative to
                the source templates directory, or absolute.
            destination: Path to the directory to press into. Either relative to the
                working directory, or absolute.

        Returns:
            The number of templates pressed, which is 0 when there was nothing to press.

        Raises:
            DestinationIsProtectedError: When a destination is the same file as a
                protected file, by any name.

            DestinationIsTemplateError: When a destination is the same file as any
                template that the walk finds, even outside `directory`, by any name.

            DestinationWithinDirectoryError: When `destination`, followed through any
                symlinks along it, is `directory` or within it. Symlinks within the
                destination aren't followed.

            FileNotFoundError: When `directory` doesn't exist.

            NotADirectoryError: When `directory` isn't a directory.

            OSError: When the directory can't be walked, a template can't be read, or a
                result can't be written, like when permissions deny it, symlinks loop,
                or a file is in the way.

            TemplateNotInDirectoryError: When `directory` leads outside the source
                templates directory.

            TemplateNotPressableError: When a template isn't a regular file, or it or a
                template that it references isn't UTF-8.

            jinja2.TemplateError: When a template can't be found or rendered, including
                when a template that it references isn't a regular file.
        """
        name = self._name(directory)
        path = self._templates_dir / name

        # `stat` raises the operating system's own error when the directory doesn't
        # exist or can't be reached, but it's happy with a file.
        if not S_ISDIR(stat(path).st_mode):
            raise NotADirectoryError(ENOTDIR, strerror(ENOTDIR), str(path))

        destination_path = Path(destination)

        # A press must never write within the directory that it presses, or the next
        # press would read the results as templates. This is stricter than it needs to
        # be when `.platenignore` files ignore the destination, but Mosey can't tell us
        # that yet: https://github.com/cariad/mosey/issues/44
        if is_within(destination_path, path):
            raise DestinationWithinDirectoryError(path, destination_path)

        # Mosey never reads ignore-files above the directory it walks, so walk the whole
        # templates directory and keep only the files within the requested directory.
        # That way, the `.platenignore` files above the requested directory still apply.
        #
        # Finish the walk before pressing anything. Mosey lists each directory as it
        # reaches it, so a result written into the templates directory, like through a
        # symlink within the destination, could otherwise be walked and pressed too.
        walk = self._walker.walk(self._templates_dir)
        walked = [step.relative_as_posix for step in walk]
        found = identity(path)

        @cache
        def spelled(rel_dir: str) -> str | None:
            # Find the directory by identity rather than by name, because `realpath`
            # keeps the caller's spelling, which a case- or normalisation-insensitive
            # file system can store differently. Jobs share most of their parents, so
            # check each parent once.
            if identity(self._templates_dir / rel_dir) == found:
                return rel_dir

            return spelled(dirname(rel_dir)) if rel_dir else None

        jobs: list[tuple[str, Path]] = []

        for rel in walked:
            if (within := spelled(dirname(rel))) is not None:
                prefix = within + "/" if within else ""
                jobs.append((rel, destination_path / rel.removeprefix(prefix)))

        if not jobs:
            log.warning(
                "Nothing to press in %s: it's empty, or everything in it is ignored",
                path,
            )

            return 0

        # Guard every template that the walk found, and not only those being pressed,
        # so that a press into the templates directory can't overwrite a template that
        # a later one references.
        self._press(
            jobs,
            walked,
        )

        return len(jobs)

    def press_file(
        self,
        template: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        """
        Press the values into a template.

        The template is pressed to the file at `destination`, which can have any name.
        An existing file is overwritten, and missing directories are created. A regular
        file that's written gets its template's permissions, but a destination like
        `/dev/null` keeps its own.

        The template is always pressed, because `.platenignore` files never apply to
        templates that are named explicitly. A template named through a symlink is
        pressed as the file that it leads to, and Jinja names it by that file's path,
        so autoescaping follows that name.

        For example, if the source templates directory is `./templates` then:

        - `press_file("doc.md", "build/doc.md")` will read from `./templates/doc.md`
          and write to `./build/doc.md`.
        - `press_file("doc.md", "build/index.md")` will read from `./templates/doc.md`
          and write to `./build/index.md`.

        And if the source templates directory is `.` then
        `press_file("README.template", "README.md")` will press a README next to its
        template.

        Args:
            template: Path to the template to press. Either relative to the source
                templates directory, or absolute.
            destination: Path to the file to press to. Either relative to the working
                directory, or absolute.

        Raises:
            DestinationIsProtectedError: When `destination` is the same file as a
                protected file, by any name.

            DestinationIsTemplateError: When `destination` is the same file as
                `template`, by any name.

            OSError: When the template can't be read or the result can't be written,
                like when `destination` is a directory, permissions deny it, or
                symlinks loop.

            TemplateNotInDirectoryError: When `template` leads outside the source
                templates directory.

                Templates must exist within the source templates directory so that
                referenced templates (`extends`, `include`, `import`, etc) can be
                resolved relative to that directory.

            TemplateNotPressableError: When `template` isn't a regular file (like a
                directory or a FIFO), or it or a template that it references isn't
                UTF-8.

            jinja2.TemplateError: When the template can't be found or rendered,
                including when a template that it references isn't a regular file.
        """
        name = self._name(template)

        self._press(
            [(name, Path(destination))],
            [name],
        )

    @property
    def templates_dir(self) -> Path:
        """Path to the source templates directory."""
        return self._templates_dir
