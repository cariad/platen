from collections.abc import Callable, Iterable, Mapping
from logging import getLogger
from os import PathLike
from pathlib import Path, PurePosixPath
from shutil import copymode
from typing import Any

from jinja2 import (
    Environment,
    StrictUndefined,
    Template,
    TemplateNotFound,
    select_autoescape,
)
from rootle import as_spelled, identity, relative_within, resolve
from rootle.types import Identity

from .exceptions import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from .loader import Loader
from .walker import walker

log = getLogger(__name__)


def _identities(paths: Iterable[Path]) -> dict[Identity, Path]:
    """
    Identify the files at paths.

    Args:
        paths: Paths to the files to identify.

    Returns:
        Each path that a file exists at, keyed by the file's identity (see
        `rootle.identity`). Where paths share an identity, the first is kept.
    """
    identities: dict[Identity, Path] = {}

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

    Call `.templates()` to find the templates that `.press(destination)` would press,
    without pressing them, read `.loaded` to find every template that a press has read,
    including those that it references, and read `.written` to find every file that a
    press has written.

    `templates_dir` is the root of a tree of templates:

    - Jinja resolves referenced templates (`extends`, `include`, `import`, etc) relative
      to it.
    - Pressing a directory applies the `.platenignore` files from `templates_dir`
      down, so that those above that directory still apply.
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
        on_load: Called with the path to every template file that a press loads or tries
            to load, as `loaded` names it, just before the press reads it, like to look
            at the file as it was pressed.

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
        on_load: Callable[[Path], object] | None = None,
        protect: Iterable[PathLike[str] | str] = (),
    ) -> None:
        self._templates_dir = Path(templates_dir).resolve()
        self._values = values
        # Anchor relative paths now, like the templates directory, so that changing the
        # working directory before a press can't change which files are protected. They
        # aren't resolved until each press, so that they're checked where they lead.
        self._protect = tuple(Path(p).absolute() for p in protect)

        self._loader = Loader(
            self._templates_dir,
            on_load,
        )

        self._written: set[Path] = set()

        self._env = Environment(
            autoescape=select_autoescape(),
            keep_trailing_newline=True,
            loader=self._loader,
            lstrip_blocks=True,
            trim_blocks=True,
            undefined=StrictUndefined,
        )

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
        path = resolve(self._templates_dir / named)

        if (relative := relative_within(path, self._templates_dir)) is None:
            raise TemplateNotInDirectoryError(path, self._templates_dir)

        return relative.as_posix()

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

            # Record the destination before writing it, so that one that a failed write
            # left half-written is recorded too.
            self._written.add(destination)

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
            path = self._templates_dir / name

            if not path.exists():
                raise

            raise TemplateNotPressableError(path, "it isn't a regular file") from None

    @property
    def loaded(self) -> frozenset[Path]:
        """Path to every template that this `Platen` has loaded or tried to load."""
        return frozenset(self._loader.loaded)

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

        The `.platenignore` files above the directory, up to the source templates
        directory, still apply.

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
                template that a walk of the source templates directory finds, even
                outside `directory`, by any name.

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
        # `_name` keeps the caller's spelling, or a symlink's, which a case- or
        # normalisation-insensitive file system can store differently, but walks only
        # start in a directory that's spelled exactly.
        name = as_spelled(self._name(directory), self._templates_dir).as_posix()
        path = self._templates_dir / name

        walker().check_walk_can_start(
            self._templates_dir,
            start=name,
        )

        destination_path = Path(destination)

        # A press must never write within the directory that it presses, or the next
        # press would read the results as templates. This is stricter than it needs to
        # be when `.platenignore` files ignore the destination, but Mosey can't tell us
        # that yet: https://github.com/cariad/mosey/issues/44
        if relative_within(destination_path, path) is not None:
            raise DestinationWithinDirectoryError(path, destination_path)

        # Guard every template in the templates directory, and not only those being
        # pressed, so that a press into the templates directory can't overwrite a
        # template that a later one references.
        #
        # That walks the whole templates directory anyway, so take the templates to
        # press from the same walk, rather than walking the requested directory again. A
        # walk yields exactly what a walk that starts in the requested directory would
        # within it, so long as the directory is spelled exactly as the walk lists it,
        # which `as_spelled` sees to: https://cariad.github.io/mosey/starting-directories/
        #
        # Finish the walk before pressing anything. Mosey lists each directory as it
        # reaches it, so a result written into the templates directory, like through a
        # symlink within the destination, could otherwise be walked and pressed too.
        templates = self.templates()

        jobs = [
            (
                template,
                destination_path / PurePosixPath(template).relative_to(name),
            )
            for template in templates
            if PurePosixPath(template).parent.is_relative_to(name)
        ]

        if not jobs:
            log.warning(
                "Nothing to press in %s: it's empty, or everything in it is ignored",
                path,
            )

            return 0

        self._press(
            jobs,
            templates,
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

    def templates(self) -> list[str]:
        """
        Name every template within the source templates directory that `press()` would
        press, without pressing anything.

        Returns:
            POSIX-style path to each template, relative to the source templates
            directory, in walk order: https://cariad.github.io/mosey/walk-order/

        Raises:
            OSError: When the directory can't be walked, or when a `.platenignore` file
                can't be read.
        """
        return [step.relative_as_posix for step in walker().walk(self._templates_dir)]

    @property
    def templates_dir(self) -> Path:
        """Path to the source templates directory."""
        return self._templates_dir

    @property
    def written(self) -> frozenset[Path]:
        """
        Path to every file that this `Platen` has written to or tried to write to, as
        each destination was given.
        """
        return frozenset(self._written)
