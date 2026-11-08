from os.path import isabs
from traceback import walk_tb
from typing import Any

from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError
from ruamel.yaml.error import YAMLError

from ..exceptions import PlatenError
from .errors import CommandError


def describe(error: Exception) -> str:
    """
    Describe an error on one line, naming paths the way the error names them.

    Args:
        error: Error to describe.

    Returns:
        The description.
    """
    if isinstance(error, (CommandError, PlatenError)):
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

    if (where := location(error)) is not None:
        message = f"{message} ({where})"

    # Messages from other libraries can span lines, and so can the paths that Platen's
    # own errors name without escaping them.
    lines = (line.strip() for line in message.splitlines())
    return " ".join(line for line in lines if line)


def escape(path: str) -> str:
    """
    Escape a path that's shown without quotes, so that it can't break its line.

    Args:
        path: Path to escape.

    Returns:
        The path, escaped like `repr` escapes it, but without quotes.
    """
    return repr(path)[1:-1]


def location(error: BaseException) -> str | None:
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

    return f"{escape(path)}, line {line}"


def yaml_problem(error: YAMLError, path: str) -> str:
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
    where = f"line {mark.line + 1}, column {mark.column + 1}"
    return f"{described} ({escape(path)}, {where})"
