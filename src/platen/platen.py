from collections.abc import Iterable, Mapping
from functools import cache
from logging import getLogger
from os import PathLike, fspath, getcwd, stat
from os.path import basename, join, realpath
from pathlib import Path
from shutil import copyfile, copymode
from stat import S_ISDIR, S_ISREG
from typing import Any, NamedTuple

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)
from mosey import Mosey

from .exceptions import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from .files import identity, is_resolved_within, os_error, sniff
from .types import Job

IGNORE_FILENAME = ".platenignore"
"""
Name of the files that list patterns of paths to ignore.

See https://cariad.github.io/mosey/ignore-files/ for the rules.
"""

log = getLogger(__name__)


class _Arguments(NamedTuple):
    """The checked arguments of a press."""

    destination: str
    """
    The destination as it was given. Callers make it absolute, but don't resolve it, so
    that errors and logs name it as it was given.
    """

    is_dir: bool
    """`True` when the template, or directory of templates, is a directory."""

    path: Path
    """
    Path to the template, or directory of templates, within the source templates
    directory.
    """

    rel: Path
    """`path`, relative to the source templates directory."""


def _assert_traversable(path: Path) -> None:
    """
    Assert that a path never steps through a file as if it were a directory.

    Destinations are resolved with `realpath`, which steps up with ".." from anything,
    but the operating system refuses to step through a file: "blocker/../result.md" is
    refused when `blocker` is a file. A ".." that steps up out of a directory that
    doesn't exist yet is still allowed, as it would be once the directory existed.

    Args:
        path: Absolute path to check.

    Raises:
        NotADirectoryError: When a part of `path` that's followed by another part is a
            file rather than a directory.
        OSError: When a part of `path` can't be checked, like when permissions deny it
            or symlinks loop.
    """
    current = Path(path.anchor)

    for part in path.parts[1:]:
        try:
            is_dir = S_ISDIR(stat(current).st_mode)
        except FileNotFoundError:
            # A directory that doesn't exist yet can be stepped into, and up out of.
            is_dir = True

        if not is_dir:
            raise os_error(NotADirectoryError, path)

        current = Path(realpath(current / part))


def _job(destination: Path, name: str) -> Job:
    """
    Make a job, resolving where its result will be written.

    Args:
        destination: Absolute path to the file to write, as it was given.
        name: POSIX-style path to the template, relative to the source templates
            directory.

    Returns:
        The job.
    """
    # `realpath` steps up with ".." the same way the operating system will once any
    # missing directories are created, and never raises for a loop of symlinks.
    return Job(
        destination=destination,
        name=name,
        parent=Path(realpath(destination.parent)),
        resolved=Path(realpath(destination)),
    )


def _not_empty(path: PathLike[str] | str) -> str:
    """
    Get a path as a string, refusing an empty string.

    `pathlib` quietly reads an empty string as ".", which would press the whole
    templates directory, or press into the working directory. The operating system
    refuses an empty path, so we do too.

    An empty `Path` can't be refused, though: `Path("")` is already `Path(".")` before
    Platen sees it, so it means the working directory, just like `Path(".")`.

    Args:
        path: Path to check.

    Returns:
        The path as a string.

    Raises:
        FileNotFoundError: When the path is an empty string.
    """
    value = fspath(path)

    if not value:
        raise os_error(FileNotFoundError, value)

    return value


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

    Text files are pressed as templates, and binary files are copied as-is.

    When walking a directory, paths matched by `.platenignore` files are neither pressed
    nor copied. `.platenignore` files use `.gitignore`-style patterns: see Mosey's
    documentation for exact rules: https://cariad.github.io/mosey/ignore-files/

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
            loader=FileSystemLoader(self._templates_dir),
            lstrip_blocks=True,
            trim_blocks=True,
            undefined=StrictUndefined,
        )

        self._mosey = Mosey(ignore_filename=IGNORE_FILENAME)

    def _arguments(
        self,
        named: PathLike[str] | str,
        destination: PathLike[str] | str,
        *,
        refuse_symlinks: bool,
    ) -> _Arguments:
        """
        Check the arguments of a press.

        Args:
            named: Path to the template, or directory of templates, to press. Either
                relative to the source templates directory, or absolute.
            destination: Path to press to. Either relative to the working directory, or
                absolute.
            refuse_symlinks: `True` to refuse `named` when it's a symlink, or within
                one, because it's a directory to walk.

        Returns:
            The checked arguments.

        Raises:
            FileNotFoundError: When `named` or `destination` is an empty string, or
                `named` doesn't exist.
            NotADirectoryError: When `named` is within a file, or when refusing symlinks
                and `named` is a symlink or within one.
            OSError: When `named` can't be read, like when permissions deny it or
                symlinks loop.
            TemplateNotInDirectoryError: When `named` is not a path within the source
                templates directory, or a symlink leads it outside.
        """
        name = _not_empty(named)
        given = _not_empty(destination)

        path = self._named_path(name)
        rel = path.relative_to(self._templates_dir)

        # The walk never descends into symlinks, so it would find nothing to press
        # within a directory named through one. Refuse it instead, naming the outermost
        # symlink.
        if refuse_symlinks:
            for part in (*reversed(rel.parents[:-1]), rel):
                within = self._templates_dir / part

                if within.is_symlink():
                    raise os_error(
                        NotADirectoryError,
                        within,
                        reason="Platen never walks into symlinks",
                    )

        # Check what's named the way the operating system sees it, rather than as
        # `_named_path` tidied it, so that a path like "doc.md/" or "doc.md/.." is
        # refused.
        is_dir = S_ISDIR(stat(join(self._templates_dir, name)).st_mode)

        return _Arguments(destination=given, is_dir=is_dir, path=path, rel=rel)

    def _assert_pressable(self, jobs: list[Job], directory: Path | None) -> None:
        """
        Assert that the jobs can be pressed without damaging the templates, and without
        failing partway through writing.

        Files are compared by identity rather than by path, so that symlinks, hard links
        and case-insensitive file systems can't hide a collision with a file that
        already exists.

        Args:
            jobs: Jobs to check.
            directory: Path to the directory being pressed, or `None` when a single
                template is being pressed.

        Raises:
            DestinationIsProtectedError: When a job's destination is the same file as a
                protected file.
            DestinationIsTemplateError: When a job's destination is the same file as any
                job's template.
            DestinationWithinDirectoryError: When a job's destination, followed through
                any symlinks, is within `directory`.
            FileExistsError: When two jobs' destinations are the same file.
            FileNotFoundError: When `directory` no longer exists, or a job's destination
                is a symlink into a directory that doesn't exist.
            IsADirectoryError: When a job's destination is a directory.
            NotADirectoryError: When a job's destination is within a file, or within
                another job's destination.
            TemplateNotPressableError: When a job's template is neither a regular file
                nor a directory, like a symlink to a FIFO or a device.
        """
        templates: dict[tuple[int, int], Path] = {}
        protected: dict[tuple[int, int], Path] = {}
        results = {job.resolved for job in jobs}
        claimed: dict[Path | tuple[int, int], Path] = {}
        walked: tuple[int, int] | None = None

        if directory is not None:
            # Nothing can be checked against a directory that's gone since it was
            # walked, and a safety check that can't be made must fail rather than pass.
            walked = identity(directory)

            if walked is None:
                raise os_error(FileNotFoundError, directory)

        @cache
        def leads_within(parent: Path) -> bool:
            # Jobs in a directory press share most of their parents, so check each
            # parent once.
            return is_resolved_within(parent, walked)

        for job in jobs:
            template = self._templates_dir / job.name

            # A template that doesn't exist can't be overwritten. Pressing it will raise
            # `FileNotFoundError`.
            if (found := identity(template)) is None:
                continue

            templates[found] = template

            # Reading a FIFO would block, and reading a device might never end, so they
            # must be refused before any template is read. Reading a directory raises
            # `IsADirectoryError`. Look where `identity` looked.
            mode = stat(realpath(template)).st_mode

            if not S_ISREG(mode) and not S_ISDIR(mode):
                raise TemplateNotPressableError(template, "it isn't a regular file")

        for path in self._protect:
            # Likewise, a protected file that doesn't exist can't be overwritten.
            if (found := identity(path)) is not None:
                protected[found] = path

        for job in jobs:
            # A press must never write within the directory it walks, even when a
            # symlink within the destination leads back into it. This is stricter than
            # it needs to be when `.platenignore` files ignore the destination, but
            # Mosey can't tell us that yet: https://github.com/cariad/mosey/issues/44
            found = identity(job.resolved)

            if directory is not None and (
                found == walked or leads_within(job.resolved.parent)
            ):
                raise DestinationWithinDirectoryError(directory, job.destination)

            if found in templates:
                raise DestinationIsTemplateError(templates[found], job.destination)

            if found in protected:
                raise DestinationIsProtectedError(protected[found], job.destination)

            # Check where the destination really leads now, rather than let the
            # operating system fail partway through writing.
            if job.resolved.is_dir():
                raise os_error(IsADirectoryError, job.destination)

            # `_write` creates the destination's resolved parent, so the nearest parent
            # that already exists must be a directory.
            ancestor = job.parent

            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent

            if not ancestor.is_dir():
                raise os_error(NotADirectoryError, job.destination)

            # A destination that's a symlink leads elsewhere, and the operating system
            # creates the file there. That directory must already exist, unless `_write`
            # creates it.
            within = job.resolved.parent

            if within not in (job.parent, *job.parent.parents) and not within.is_dir():
                error = NotADirectoryError if within.exists() else FileNotFoundError
                raise os_error(error, job.destination)

            # A symlink within the destination could lead into another job's result,
            # which would then be in the way of a directory. On a case-insensitive file
            # system, a spelling that differs from the result only in case is caught
            # above once the result exists, but not on the press that first creates it.
            if any(p in results for p in job.resolved.parents):
                raise os_error(NotADirectoryError, job.destination)

            # Two jobs mustn't be pressed to the same file, or one result would
            # overwrite the other. Compare where the destinations lead, and the files
            # themselves when they exist, to see through symlinks and hard links.
            for key in (job.resolved, found):
                if key is not None:
                    if key in claimed:
                        raise os_error(FileExistsError, job.destination, claimed[key])

                    claimed[key] = job.destination

    def _named_path(self, template: PathLike[str] | str) -> Path:
        """
        Get the path to a named template, or directory of templates, within the source
        templates directory.

        Symlinks within the templates directory keep their names, so that Jinja loads a
        template by the name it's given, a directory is walked by the name it's given,
        and the path's final symlink is never followed. Symlinks outside the templates
        directory that lead into it are followed.

        Args:
            template: Path to the template or directory, either relative to the source
                templates directory or absolute.

        Returns:
            Path to the template or directory within the source templates directory.

        Raises:
            TemplateNotInDirectoryError: When `template` is not a path within the source
                templates directory, or a symlink leads it outside.
        """
        path = self._templates_dir

        # Step up with ".." the same way the operating system does: from a symlink's
        # target, rather than from the directory that holds the symlink.
        for part in Path(template).parts:
            if part == "..":
                path = (Path(realpath(path)) if path.is_symlink() else path).parent
            else:
                path /= part

        # An absolute path might reach the templates directory through a symlink (like
        # "/var" to "/private/var" on macOS), so find where it first leads into the
        # templates directory, and keep the rest as it's named.
        if not path.is_relative_to(self._templates_dir):
            for entry in (*reversed(path.parents), path):
                resolved = Path(realpath(entry))

                if resolved.is_relative_to(self._templates_dir):
                    path = resolved / path.relative_to(entry)
                    break

        # Check where the path really leads, so that it can't be outside the templates
        # directory, even through a symlink. `Path.resolve()` raises `RuntimeError` for
        # a loop of symlinks on Python 3.11 and 3.12, but `realpath` never raises, so a
        # loop fails the same way on every version: with `OSError` when it's opened.
        resolved = Path(realpath(path))

        if not resolved.is_relative_to(self._templates_dir):
            raise TemplateNotInDirectoryError(
                resolved,
                self._templates_dir,
            )

        return path

    def _plan_directory(
        self,
        path: Path,
        rel: Path,
        destination: Path,
    ) -> list[Job]:
        """
        Plan the press of every template within a directory.

        Args:
            path: Path to the directory within the source templates directory.
            rel: Path to the directory relative to the source templates directory.
            destination: Absolute path to the directory to press into.

        Returns:
            A job for every template within the directory that `.platenignore` files
            don't ignore, in walk order.

        Raises:
            DestinationWithinDirectoryError: When `destination` is the directory or
                within it.
            NotADirectoryError: When `destination` exists but isn't a directory.
        """
        # Resolve the destination first, so that a ".." after a directory that doesn't
        # exist yet steps up the same way it will once the directory is created.
        resolved = Path(realpath(destination))

        # Check the destination as a whole before walking, so that the error names the
        # destination as it was given. `_assert_pressable` checks every file's
        # destination too, because a symlink within the destination could lead back
        # into the directory.
        if is_resolved_within(resolved, identity(path)):
            raise DestinationWithinDirectoryError(path, destination)

        if resolved.exists() and not resolved.is_dir():
            raise os_error(NotADirectoryError, destination)

        # Mosey never reads ignore-files above the directory it walks, so walk the whole
        # templates directory and keep only the files within the requested directory.
        # That way, the `.platenignore` files above the requested directory still apply.
        rel_path = rel.as_posix()
        prefix = "" if rel_path == "." else rel_path + "/"
        jobs: list[Job] = []

        for step in self._mosey.walk(self._templates_dir):
            if step.name == IGNORE_FILENAME:
                continue

            if step.relative_as_posix.startswith(prefix):
                name = step.relative_as_posix
                jobs.append(_job(destination / name.removeprefix(prefix), name))

        return jobs

    def _press(self, jobs: list[Job], directory: Path | None) -> None:
        """
        Check, render and write jobs.

        Nothing is written until every job has been checked and rendered.

        Args:
            jobs: Jobs to press.
            directory: Path to the directory being pressed, or `None` when a single
                template is being pressed.
        """
        self._assert_pressable(jobs, directory)

        # Render every template before writing anything, so that a template that fails
        # to render leaves nothing part-pressed, and no write can change a file that a
        # later render reads (like an included template).
        bodies = [self._render(job.name) for job in jobs]

        for job, body in zip(jobs, bodies, strict=True):
            self._write(job, body)

    def _render(self, name: str) -> str | None:
        """
        Render a template.

        Args:
            name: POSIX-style path to the template, relative to the source templates
                directory.

        Returns:
            The rendered document, or `None` when the template is a binary file that
            must be copied as-is.

        Raises:
            TemplateNotPressableError: When the template is text, but not UTF-8.
        """
        template = self._templates_dir / name

        if not sniff(template).is_text:
            return None

        log.debug("Pressing %s", template)

        try:
            loaded = self._env.get_template(name)
        except UnicodeDecodeError as error:
            # Jinja reads templates as UTF-8, and its error doesn't name the file.
            reason = f"it isn't valid UTF-8 ({error.reason} at byte {error.start})"
            raise TemplateNotPressableError(template, reason) from error

        return loaded.render(self._values)

    def _write(self, job: Job, body: str | None) -> None:
        """
        Write a job's result.

        Args:
            job: Job to write.
            body: The rendered document, or `None` to copy the template as-is.
        """
        template = self._templates_dir / job.name

        # Write into the destination's resolved parent, which is where the checks
        # looked, so that a ".." after a directory that doesn't exist yet can't leave a
        # stray directory behind. The destination itself isn't resolved, so the
        # operating system follows a final symlink (like `/dev/stdout`) itself.
        job.parent.mkdir(exist_ok=True, parents=True)
        destination = job.parent / job.destination.name

        try:
            if body is None:
                log.debug("Copying binary file %s", template)
                copyfile(template, destination)
                log.info("Copied %s to %s", job.name, job.destination)
            else:
                destination.write_text(
                    body,
                    encoding="utf-8",
                    newline="\n",
                )

                log.info("Pressed %s to %s", job.name, job.destination)

            # Replicate the original file permissions, but only on a regular file. A
            # destination like `/dev/null` or a terminal must keep its own permissions.
            if destination.is_file():
                copymode(template, destination)
        except OSError as error:
            if error.filename != str(destination):
                raise

            # Name the destination as it was given, like every other error about it,
            # rather than where it resolved to.
            raise OSError(
                error.errno,
                error.strerror,
                str(job.destination),
            ) from error

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
            The number of templates pressed or copied, which is 0 when there was nothing
            to press.
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
        and nothing is ever deleted.

        `directory` must be a directory, and `destination` must be a directory or not
        exist yet. Anything else is refused rather than guessed at.

        Text files are pressed as templates and binary files are copied as-is. Every
        regular file that's written gets its template's permissions, but a destination
        like `/dev/null` keeps its own.

        Paths matched by `.platenignore` files are neither pressed nor copied.
        `.platenignore` files use `.gitignore`-style patterns: see Mosey's documentation
        for exact rules: https://cariad.github.io/mosey/ignore-files/

        Pressing a directory walks the whole source templates directory, so that the
        `.platenignore` files above the directory still apply. Keep templates in a
        dedicated directory, rather than, say, the root of a project, so that walks
        stay quick.

        Symlinks within the templates directory are pressed like files, to their own
        paths within the destination rather than their targets'. They're never walked
        into, so neither a symlink to a directory nor a directory within one can be
        pressed. To keep a symlink to a directory out of a walk, list it in a
        `.platenignore` file like a file, without a trailing slash.

        Nothing is written until every template has been found, checked against its
        destination, and rendered. Only an error while writing, like a permissions
        error, can leave a press part-done.

        For example, if the source templates directory is `./templates` then
        `press_directory("foo", "build/foo")` will press every file within
        `./templates/foo` to `./build/foo`.

        Args:
            directory: Path to the directory of templates to press. Either relative to
                the source templates directory, or absolute.
            destination: Path to the directory to press into. Either relative to the
                working directory, or absolute.

        Returns:
            The number of templates pressed or copied, which is 0 when there was nothing
            to press.

        Raises:
            DestinationIsProtectedError: When a destination is the same file as a
                protected file, by any name.

            DestinationIsTemplateError: When a destination is the same file as a
                template that's being pressed, including through a symlink, a hard link,
                or a spelling that a case-insensitive file system treats as the same
                name.

            DestinationWithinDirectoryError: When a destination, followed through any
                symlinks, is `directory` or within it.

            FileExistsError: When two templates would be pressed to the same file, like
                through a symlink or hard link within the destination.

            FileNotFoundError: When `directory` or `destination` is an empty string,
                `directory` doesn't exist, it holds a broken symlink that
                `.platenignore` files don't ignore, or a destination is a symlink into a
                directory that doesn't exist.

            IsADirectoryError: When `directory` holds a symlink to a directory that
                `.platenignore` files don't ignore, or a directory is where a template
                must be pressed to.

            NotADirectoryError: When `directory` isn't a directory, is a symlink, or is
                within a symlink to a directory. Also when `destination` isn't a
                directory or steps through a file, like "blocker/../build", or a file is
                where a directory must be.

            OSError: When a template can't be read or a result can't be written, like
                when permissions deny it or symlinks loop.

            TemplateNotInDirectoryError: When `directory` is not a path within the
                source templates directory, or a symlink leads it outside.

            TemplateNotPressableError: When `directory` holds a template that's neither
                a regular file nor a directory, like a symlink to a FIFO or a device, or
                a text template that isn't UTF-8. Nothing has been written.

            jinja2.TemplateError: When a template can't be rendered. Nothing has been
                written.
        """
        arguments = self._arguments(directory, destination, refuse_symlinks=True)

        if not arguments.is_dir:
            raise os_error(NotADirectoryError, arguments.path)

        destination_path = Path(arguments.destination).absolute()
        _assert_traversable(destination_path)

        jobs = self._plan_directory(arguments.path, arguments.rel, destination_path)

        if not jobs:
            log.warning(
                "Nothing to press in %s: it's empty, or everything in it is ignored",
                arguments.path,
            )

            return 0

        self._press(jobs, arguments.path)
        return len(jobs)

    def press_file(
        self,
        template: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        """
        Press the values into a template.

        The template is pressed to the file at `destination`, which can have any name.
        An existing file is overwritten, and missing directories are created.

        `template` must be a file, and `destination` must be a file or not exist yet.
        Anything else is refused rather than guessed at.

        A text file is pressed as a template and a binary file is copied as-is. A
        regular file that's written gets its template's permissions, but a destination
        like `/dev/null` keeps its own.

        The template is always pressed, because `.platenignore` files never apply to
        templates that are named explicitly.

        Nothing is written until the template has been found, checked against its
        destination, and rendered.

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
                `template`, including through a symlink, a hard link, or a spelling that
                a case-insensitive file system treats as the same name.

            FileNotFoundError: When `template` or `destination` is an empty string,
                `template` doesn't exist, or `destination` is a symlink into a directory
                that doesn't exist.

            IsADirectoryError: When `template` is a directory or a symlink to one, or
                `destination` is a directory or names one, like "build/", "build/." or
                "build/x/..".

            NotADirectoryError: When `template` is within a file, or `destination` is,
                or `destination` steps through a file, like "blocker/../result.md".

            OSError: When the template can't be read or the result can't be written,
                like when permissions deny it or symlinks loop.

            TemplateNotInDirectoryError: When `template` is not a path within the source
                templates directory, or a symlink leads it outside.

                Templates must exist within the source templates
                directory so that referenced templates (`extends`,
                `include`, `import`, etc) can be resolved relative to
                that directory.

            TemplateNotPressableError: When `template` is neither a regular file nor a
                directory, like a FIFO or a device, or it's text that isn't UTF-8.
                Nothing has been written.

            jinja2.TemplateError: When the template can't be rendered. Nothing has been
                written.
        """
        arguments = self._arguments(template, destination, refuse_symlinks=False)

        if arguments.is_dir:
            raise os_error(IsADirectoryError, arguments.path)

        # A destination like "build/", "build/." or "build/x/.." names a directory, but
        # `pathlib` would drop the trailing separator from "build/" and press a file
        # named "build".
        if basename(arguments.destination) in ("", ".", ".."):
            raise os_error(
                IsADirectoryError,
                join(getcwd(), arguments.destination),
            )

        destination_path = Path(arguments.destination).absolute()
        _assert_traversable(destination_path)

        job = _job(destination_path, arguments.rel.as_posix())
        self._press([job], None)

    @property
    def templates_dir(self) -> Path:
        """Path to the source templates directory."""
        return self._templates_dir
