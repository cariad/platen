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
    PlatenError,
    TemplateNotInDirectoryError,
)
from .files import os_error
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

    Platen names paths absolutely, and within the directories that they resolve to.

    Args:
        path: Path that the error names.
        directory: Path to the source templates directory, as it was given.
        output: Path to the file to press to, as it was given.

    Returns:
        The path as it was given, or `path` when it wasn't given.
    """
    try:
        spellings = _spellings(output)
        templates_dir = Path(realpath(directory or "."))
    except OSError:
        # Relative paths can't be compared without a working directory.
        return path

    if path in spellings:
        return spellings[path]

    if Path(path).is_relative_to(templates_dir):
        return _name(path, templates_dir, directory)

    return path


def _describe(
    error: Exception,
    template: str,
    values_path: str,
    output: str,
) -> str:
    """
    Describe an error on one line, naming paths the way they were given.

    Args:
        error: Error to describe.
        template: Path to the template, as it was given.
        values_path: Path to the values file, as it was given.
        output: Path to the file to press to, as it was given.

    Returns:
        The description.
    """
    directory = dirname(template)

    if isinstance(error, DestinationIsProtectedError):
        message = (
            f"Destination {output!r} is the same file as values file {values_path!r}"
        )
    elif isinstance(error, DestinationIsTemplateError):
        message = f"Destination {output!r} is the same file as template {template!r}"
    elif isinstance(error, TemplateNotInDirectoryError):
        message = (
            f"Template {template!r} leads to {str(error.template_path)!r}, outside its "
            f"directory {directory or '.'!r}"
        )
    elif isinstance(error, PlatenError):
        message = str(error)
    elif type(error) is TemplateNotFound:
        # Jinja's message for a missing template names the whole search path absolutely.
        message = f"Template {error.name!r} not found"
    elif isinstance(error, TemplateError):
        message = error.message or type(error).__name__
    elif isinstance(error, OSError) and error.strerror is not None:
        paths = " -> ".join(
            repr(_as_given(path, directory, output))
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

    templates_dir = Path(realpath(directory or "."))
    return f"{_escape(_name(path, templates_dir, directory))}, line {line}"


def _name(
    path: str,
    templates_dir: Path,
    directory: str,
) -> str:
    """
    Name a path within the source templates directory the way the user would know it.

    Args:
        path: Absolute path within the source templates directory.
        templates_dir: Path to the source templates directory.
        directory: Path to the source templates directory, as it was given.

    Returns:
        The path, relative to the directory as it was given.
    """
    return join(directory, relpath(path, templates_dir))


def _press(
    template: str,
    values_path: str,
    output: str,
) -> None:
    """
    Press values from a YAML file into a template.

    The template's directory is the source templates directory, and the values file is
    protected from being pressed over.

    Args:
        template: Path to the template to press.
        values_path: Path to the YAML file of values to press.
        output: Path to the file to press to.

    Raises:
        Exception: When anything stops the press. Nothing has been written, unless
            writing itself failed.
    """
    # Check the template as it was given, so that the error names it that way, so that
    # a path like "doc.md/" is refused rather than tidied up, and so that a template
    # like a FIFO is refused before anything reads it.
    mode = stat(template).st_mode

    if S_ISDIR(mode):
        raise os_error(IsADirectoryError, template)

    if not S_ISREG(mode):
        raise _Error(f"Template {template!r} is not a regular file")

    with open(values_path, "rb") as stream:
        values = _load_values(stream, values_path)

    # The template is a file, so its name is never empty.
    directory, name = split(template)
    platen = Platen(directory or ".", values, protect=[values_path])
    platen.press_file(name, output)


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
    Press values from a YAML file into a template, on the command line.

    Nothing is printed on success. Any failure is printed as a single line, without a
    traceback.

    Args:
        argv: Command-line arguments, without the program's name. Defaults to the
            arguments that the program was run with.

    Returns:
        0 on success, 1 on failure, or 130 when interrupted.

    Raises:
        SystemExit: When the arguments are wrong, with 2, or help is asked for, with 0.
    """
    parser = ArgumentParser(
        description="Press structured data into a document.",
        prog="platen",
    )

    parser.add_argument(
        "template",
        help=(
            "path to the template to press; templates that it includes are found "
            "relative to its directory"
        ),
    )

    parser.add_argument(
        "values",
        help="path to the YAML file of values to press into the template",
    )

    parser.add_argument(
        "output",
        help="path to the file to press to",
    )

    arguments = parser.parse_args(argv)

    try:
        _press(arguments.template, arguments.values, arguments.output)
    except KeyboardInterrupt:
        # Stop quietly, with the exit code that shells give an interrupted command.
        return 130
    except Exception as error:
        message = _describe(
            error,
            arguments.template,
            arguments.values,
            arguments.output,
        )

        print(f"{parser.prog}: error: {message}", file=sys.stderr)
        return 1

    return 0
