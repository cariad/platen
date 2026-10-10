import sys
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from os import stat
from os.path import isabs, split
from stat import S_ISDIR
from traceback import walk_tb
from typing import Any, BinaryIO, cast
from warnings import catch_warnings

from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .exceptions import PlatenError
from .platen import Platen


class _Error(PlatenError):
    """A failure that's already described for the command line."""


def _describe(error: Exception) -> str:
    """
    Describe an error on one line, naming paths the way the error names them.

    Args:
        error: Error to describe.

    Returns:
        The description.
    """
    if isinstance(error, PlatenError):
        # Platen's errors, and the command line's own, describe themselves.
        message = str(error)
    elif type(error) is TemplateNotFound:
        # Jinja's message for a missing template names the whole search path absolutely.
        message = f"Template {error.name!r} not found"
    elif isinstance(error, TemplateError):
        message = error.message or type(error).__name__
    elif isinstance(error, OSError) and error.strerror is not None:
        # Python's own description leads with the error number, which means nothing to
        # most people.
        paths = " -> ".join(
            repr(path) for path in (error.filename, error.filename2) if path is not None
        )

        message = f"{error.strerror}: {paths}" if paths else error.strerror
    else:
        message = f"{type(error).__name__}: {error}"

    if (location := _location(error)) is not None:
        message = f"{message} ({location})"

    # Messages from other libraries can span lines, and so can the paths that Platen's
    # own errors name without escaping them.
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


def _location(error: BaseException) -> str | None:
    """
    Find where in a template an error was raised.

    Jinja rewrites the traceback of an error raised while loading or rendering a
    template, so that the template's frames carry its file name and line number. The
    innermost of those frames is the template that raised it, like an included one.

    Args:
        error: Error to locate.

    Returns:
        The template's path, as Jinja names it, and the line, or `None` when the error
        wasn't raised in a template.
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

    # Platen loads templates from its resolved templates directory, so Jinja names every
    # template file absolutely. Name nothing else, rather than guess at a file.
    if not isabs(path):
        return None

    return f"{_escape(path)}, line {line}"


def _press(
    template: str,
    values_path: str,
    output: str,
) -> bool:
    """
    Press values from a YAML file into a template, or into every template within a
    directory.

    A template's own directory is the source templates directory, so that the templates
    that it references are found relative to it. The values file is protected from
    being pressed over.

    Args:
        template: Path to the template, or directory of templates, to press.
        values_path: Path to the YAML file of values to press.
        output: Path to the file to press the template to, or the directory to press
            every template into.

    Returns:
        `False` when there was nothing to press in the directory, otherwise `True`.

    Raises:
        Exception: When anything stops the press. The templates pressed before it
            stopped stay pressed.
    """
    # Check the template as it was given, before reading any values, so that the error
    # names it that way, and so that a path like "doc.md/" is refused rather than tidied
    # up.
    is_dir = S_ISDIR(stat(template).st_mode)

    with open(values_path, "rb") as stream:
        values = _load_values(stream, values_path)

    if is_dir:
        return Platen(template, values, protect=[values_path]).press(output) > 0

    # The template isn't a directory, so it doesn't end with a separator, and its name
    # is never empty.
    directory, name = split(template)
    platen = Platen(directory or ".", values, protect=[values_path])
    platen.press_file(name, output)
    return True


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

    try:
        pressed = _press(arguments.template, arguments.values, arguments.output)
    except KeyboardInterrupt:
        # Stop quietly, with the exit code that shells give an interrupted command.
        return 130
    except Exception as error:
        print(f"{parser.prog}: error: {_describe(error)}", file=sys.stderr)
        return 1

    if not pressed:
        # Name the directory as it was given, rather than as the library logs it.
        print(
            f"{parser.prog}: warning: Nothing to press in {arguments.template!r}: "
            "it's empty, or everything in it is ignored",
            file=sys.stderr,
        )

    return 0
