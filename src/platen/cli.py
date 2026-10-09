import sys
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from os import getcwd, stat
from os.path import dirname, isabs, join, realpath, relpath, split
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from traceback import walk_tb
from typing import Any, BinaryIO, cast
from warnings import catch_warnings

from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .exceptions import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    PlatenError,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from .platen import Platen


class _Error(PlatenError):
    """A failure that's already described for the command line."""


def _as_given(
    path: str,
    directory: str,
    output: str,
) -> str:
    """
    Name a path that an error names the way the user would know it.

    Platen names paths absolutely, and within the directories that they resolve to. A
    path that's the output, or one of its parents, is named as it was given. Otherwise,
    a path is named relative to the deepest directory that it's within, as it was given:
    the source templates directory, the output, or the directory that the output
    resolves to.

    Args:
        path: Path that the error names.
        directory: Path to the source templates directory, as it was given.
        output: Path to the file or directory to press to, as it was given.

    Returns:
        The path as it was given, or `path` when it wasn't given.
    """
    try:
        spellings = _spellings(output)

        # A directory press names its destinations within the output as it was given,
        # but writes them within the directory that the output resolves to.
        bases = (
            (_templates_dir(directory), directory),
            (Path(output).absolute(), output),
            (Path(realpath(output)), output),
        )
    except OSError:
        # Relative paths can't be compared without a working directory.
        return path

    if path in spellings:
        return spellings[path]

    named = Path(path)

    # Paths are compared as they're spelled, so a destination like
    # "templates/../build/doc.md" starts with the templates directory without being
    # within it.
    within = [
        (base, given)
        for base, given in bases
        if named.is_relative_to(base) and ".." not in named.relative_to(base).parts
    ]

    if not within:
        return path

    # The deepest directory names the path most closely, like a destination within an
    # output that's within the source templates directory. The first wins a tie.
    base, given = max(within, key=lambda b: len(b[0].parts))
    return _name(path, base, given)


def _describe(
    error: Exception,
    template: str,
    directory: str,
    name: str | None,
    values_path: str,
    output: str,
) -> str:
    """
    Describe an error on one line, naming paths the way they were given.

    Args:
        error: Error to describe.
        template: Path to the template, or directory of templates, as it was given.
        directory: Path to the source templates directory, as it was given.
        name: Name of the template within the directory, or `None` when the whole
            directory is being pressed.
        values_path: Path to the values file, as it was given.
        output: Path to the file or directory to press to, as it was given.

    Returns:
        The description.
    """
    # Each path that a Platen error names is named by what it is, rather than by how
    # it's spelled, so that a template is never named by the output's spelling.
    if isinstance(error, DestinationIsProtectedError):
        destination = _as_given(str(error.destination_path), directory, output)
        message = (
            f"Destination {destination!r} is the same file as values file "
            f"{values_path!r}"
        )
    elif isinstance(error, DestinationIsTemplateError):
        destination = _as_given(str(error.destination_path), directory, output)
        pressed = _template_as_given(error.template_path, template, directory, name)

        message = (
            f"Destination {destination!r} is the same file as template {pressed!r}"
        )
    elif isinstance(error, DestinationWithinDirectoryError):
        destination = _as_given(str(error.destination_path), directory, output)
        within = _in_templates_dir(str(error.directory), directory)

        message = (
            f"Destination {destination!r} is within {within!r}, the directory being "
            "pressed"
        )
    elif isinstance(error, TemplateNotInDirectoryError):
        message = (
            f"Template {template!r} leads to {str(error.template_path)!r}, outside its "
            f"directory {directory or '.'!r}"
        )
    elif isinstance(error, TemplateNotPressableError):
        pressed = _template_as_given(error.template_path, template, directory, name)
        message = f"Template {pressed!r} can't be pressed: {error.reason}"
    elif isinstance(error, PlatenError):
        message = str(error)
    elif type(error) is TemplateNotFound:
        # Jinja's message for a missing template names the whole search path absolutely.
        message = f"Template {error.name!r} not found"
    elif isinstance(error, TemplateError):
        message = error.message or type(error).__name__
    elif isinstance(error, OSError) and error.strerror is not None:
        # The template is checked, and the values file opened, by the names they were
        # given, so an error that names either exactly keeps that name, even when it's
        # within another directory.
        paths = " -> ".join(
            repr(
                path
                if path in (template, values_path)
                else _as_given(path, directory, output)
            )
            for path in (error.filename, error.filename2)
            if path is not None
        )

        message = f"{error.strerror}: {paths}" if paths else error.strerror
    else:
        message = f"{type(error).__name__}: {error}"

    if (location := _location(error, directory)) is not None:
        message = f"{message} ({location})"

    # Messages from other libraries can span lines, but paths are escaped so that they
    # never do.
    lines = (line.strip() for line in message.splitlines())
    return " ".join(line for line in lines if line)


def _escape(path: str) -> str:
    """
    Escape a path that's shown without quotes, so that it can't break its line.

    Args:
        path: Path to escape.

    Returns:
        The path, escaped like `repr` escapes it, but without quotes.
    """
    return repr(path)[1:-1]


def _in_templates_dir(path: str, directory: str) -> str:
    """
    Name a path within the source templates directory the way the user would know it.

    Args:
        path: Absolute path within the source templates directory, or to the directory
            itself.
        directory: Path to the source templates directory, as it was given.

    Returns:
        The path, relative to the directory as it was given, or `path` when the
        directory can't be found.
    """
    try:
        templates_dir = _templates_dir(directory)
    except OSError:
        # Relative paths can't be compared without a working directory.
        return path

    return _name(path, templates_dir, directory)


def _load_values(
    stream: BinaryIO,
    path: str,
) -> Mapping[str, Any]:
    """
    Load values from a YAML file.

    Args:
        stream: The open values file.
        path: Path to the values file, as it was given.

    Returns:
        The values. An empty file holds no values.

    Raises:
        _Error: When the file isn't valid YAML, doesn't hold a mapping, or has a key
            that isn't a string.
    """
    try:
        # ruamel.yaml warns over many lines about some documents that it loads anyway,
        # like ones that reuse an anchor.
        with catch_warnings(action="ignore"):
            values: object = YAML(typ="safe").load(stream)  # pyright: ignore[reportUnknownMemberType]
    except YAMLError as error:
        raise _Error(_yaml_problem(error, path)) from error
    except (AssertionError, RecursionError) as error:
        # ruamel.yaml asserts that it reads a document's YAML version, and recurses into
        # nested collections.
        raise _Error(f"{error} ({_escape(path)})") from error

    if values is None:
        return {}

    if not isinstance(values, Mapping):
        raise _Error(f"Values file {path!r} must hold a mapping of names to values")

    for key in cast(Mapping[object, Any], values):
        if not isinstance(key, str):
            raise _Error(f"Values file {path!r} has a key that isn't a string: {key!r}")

    return cast(Mapping[str, Any], values)


def _locate(template: str) -> tuple[str, str | None]:
    """
    Find the source templates directory, and the template to press within it.

    Args:
        template: Path to the template, or directory of templates, to press.

    Returns:
        The source templates directory, as it was given, and the name of the template
        within it. The name is `None` when `template` is the directory itself, so every
        template within it must be pressed.

    Raises:
        _Error: When `template` is neither a directory nor a regular file.
        OSError: When `template` can't be found or read.
    """
    # Check the template as it was given, so that the error names it that way, so that
    # a path like "doc.md/" is refused rather than tidied up, and so that a template
    # like a FIFO is refused before anything reads it.
    mode = stat(template).st_mode

    if S_ISDIR(mode):
        return template, None

    if not S_ISREG(mode):
        raise _Error(f"Template {template!r} is neither a directory nor a regular file")

    # The template is a file, so its name is never empty.
    return split(template)


def _location(
    error: BaseException,
    directory: str,
) -> str | None:
    """
    Find where in a template an error was raised.

    Jinja rewrites the traceback of an error raised while loading or rendering a
    template, so that the template's frames carry its file name and line number. The
    innermost of those frames is the template that raised it, like an included one.

    Args:
        error: Error to locate.
        directory: Path to the source templates directory, as it was given.

    Returns:
        The template, named relative to the directory as it was given, and the line,
        or `None` when the error wasn't raised in a template.
    """
    # Jinja marks the frames that it rewrites with this global (see jinja2/debug.py).
    # It isn't public, so the tests pin the locations that it gives.
    frames = [
        (frame, line)
        for frame, line in walk_tb(error.__traceback__)
        if "__jinja_exception__" in frame.f_globals
    ]

    if not frames:
        return None

    frame, line = frames[-1]
    path = frame.f_code.co_filename

    # Jinja leaves the file of an unclosed comment or raw block unknown, and gives its
    # path as the error's name instead.
    if path == "<unknown>" and isinstance(error, TemplateSyntaxError) and error.name:
        path = error.name

    # Name only a file that Jinja names absolutely, rather than guess at another.
    if not isabs(path):
        return None

    return f"{_escape(_in_templates_dir(path, directory))}, line {line}"


def _name(
    path: str,
    base: Path,
    given: str,
) -> str:
    """
    Name a path within a directory the way the user would know it.

    Args:
        path: Absolute path within the directory, or to the directory itself.
        base: Path to the directory.
        given: Path to the directory, as it was given.

    Returns:
        The path, relative to the directory as it was given, or the directory as it was
        given when the path is the directory itself.
    """
    rel = relpath(path, base)

    if rel == ".":
        return given or "."

    return join(given, rel)


def _press(
    directory: str,
    name: str | None,
    values_path: str,
    output: str,
) -> bool:
    """
    Press values from a YAML file into a template, or into every template within the
    source templates directory.

    The values file is protected from being pressed over.

    Args:
        directory: Path to the source templates directory.
        name: Name of the template to press within the directory, or `None` to press
            every template within it.
        values_path: Path to the YAML file of values to press.
        output: Path to the file to press the template to, or the directory to press
            every template into.

    Returns:
        `False` when there was nothing to press in the directory, otherwise `True`.

    Raises:
        Exception: When anything stops the press. Nothing has been written, unless
            writing itself failed.
    """
    with open(values_path, "rb") as stream:
        values = _load_values(stream, values_path)

    platen = Platen(directory or ".", values, protect=[values_path])

    if name is None:
        return platen.press(output) > 0

    platen.press_file(name, output)
    return True


def _spellings(path: str) -> dict[str, str]:
    """
    Map the names that Platen might give a path, and each of its parents, back to the
    way they were given.

    Platen names paths absolutely, and writes within the directory that a path's parent
    resolves to, so a path like "link/sub/out.md" might be named by the absolute path
    of "link", "link/sub" (resolved through "link") or "link/sub/out.md".

    Args:
        path: Path, as it was given.

    Returns:
        The path and each of its parents as they were given, keyed by the absolute
        paths that Platen might name them by.
    """
    spellings: dict[str, str] = {}

    while True:
        absolute = path if isabs(path) else join(getcwd(), path)
        head, tail = split(absolute)

        for key in (absolute, str(Path(absolute)), join(realpath(head), tail)):
            spellings.setdefault(key, path)

        parent = dirname(path)

        if parent in ("", path):
            return spellings

        path = parent


def _template_as_given(
    path: Path,
    template: str,
    directory: str,
    name: str | None,
) -> str:
    """
    Name a template that's being pressed the way the user would know it.

    Args:
        path: Path to the template within the source templates directory.
        template: Path to the template, or directory of templates, as it was given.
        directory: Path to the source templates directory, as it was given.
        name: Name of the template within the directory, or `None` when the whole
            directory is being pressed.

    Returns:
        The template as it was given, when it was named, or else the template within
        the directory as it was given.
    """
    if name is not None:
        # Only the template that was named is pressed.
        return template

    return _in_templates_dir(str(path), directory)


def _templates_dir(directory: str) -> Path:
    """
    Find the source templates directory.

    Args:
        directory: Path to the source templates directory, as it was given.

    Returns:
        The directory, resolved like Platen resolves it.

    Raises:
        OSError: When the directory is relative and the working directory is gone.
    """
    return Path(realpath(directory or "."))


def _yaml_problem(error: YAMLError, path: str) -> str:
    """
    Describe a problem with a values file.

    Args:
        error: The problem.
        path: Path to the values file, as it was given.

    Returns:
        The description, which might span lines.
    """
    # ruamel.yaml doesn't declare the types of its errors' attributes, and only some
    # errors are marked with where the problem is.
    context: str | None = getattr(error, "context", None)
    problem: str | None = getattr(error, "problem", None)
    mark: Any = getattr(error, "problem_mark", None)

    if mark is None:
        return str(error)

    # Marks count lines and columns from zero.
    described = ", ".join(p for p in (context, problem) if p)
    location = f"line {mark.line + 1}, column {mark.column + 1}"
    return f"{described} ({_escape(path)}, {location})"


def main(argv: Sequence[str] | None = None) -> int:
    """
    Press values from a YAML file into a template, or into every template within a
    directory, on the command line.

    Nothing is printed on success, except a warning when a directory has nothing to
    press. Any failure is printed as a single line, without a traceback.

    Args:
        argv: Command-line arguments, without the program's name. Defaults to the
            arguments that the program was run with.

    Returns:
        0 on success, 1 on failure, or 130 when interrupted.

    Raises:
        SystemExit: When the arguments are wrong, with 2, or help is asked for, with 0.
    """
    parser = ArgumentParser(
        description="Press structured data into documents.",
        prog="platen",
    )

    parser.add_argument(
        "template",
        help=(
            "path to the template to press, or to a directory of templates to press "
            "them all; included templates are found relative to the template's "
            "directory, or to that directory"
        ),
    )

    parser.add_argument(
        "values",
        help="path to the YAML file of values to press into the templates",
    )

    parser.add_argument(
        "output",
        help=(
            "path to the file to press to, or the directory to press into when the "
            "template is a directory"
        ),
    )

    arguments = parser.parse_args(argv)

    # Paths are named relative to the template's directory, as it was given, until the
    # template is found to be a directory of templates itself.
    directory, name = split(arguments.template)

    try:
        directory, name = _locate(arguments.template)
        pressed = _press(directory, name, arguments.values, arguments.output)
    except KeyboardInterrupt:
        # Stop quietly, with the exit code that shells give an interrupted command.
        return 130
    except Exception as error:
        message = _describe(
            error,
            arguments.template,
            directory,
            name,
            arguments.values,
            arguments.output,
        )

        print(f"{parser.prog}: error: {message}", file=sys.stderr)
        return 1

    if not pressed:
        # Name the directory as it was given, rather than as the library logs it.
        print(
            f"{parser.prog}: warning: Nothing to press in {arguments.template!r}: "
            "it's empty, or everything in it is ignored",
            file=sys.stderr,
        )

    return 0
