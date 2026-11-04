import sys
from argparse import SUPPRESS, Action, ArgumentParser, Namespace
from collections.abc import Mapping, Sequence
from contextlib import closing
from importlib.metadata import PackageNotFoundError, version
from os import stat
from os.path import isabs, split
from pathlib import Path
from stat import S_ISDIR
from time import sleep
from traceback import walk_tb
from typing import Any, BinaryIO, NoReturn, cast
from warnings import catch_warnings

from jinja2 import TemplateError, TemplateNotFound, TemplateSyntaxError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .exceptions import PlatenError
from .platen import Platen
from .watch import (
    Look,
    Status,
    baseline,
    directories,
    look,
    status,
    unwritten,
)


class _Error(PlatenError):
    """A failure that's already described for the command line."""


class _VersionAction(Action):
    """
    Print Platen's version and exit, like argparse's own "version" action, but only
    look the version up when it's asked for.
    """

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        try:
            installed = version("platen")
        except PackageNotFoundError:
            # Platen can run without its package's metadata, like from a source tree.
            installed = "unknown"

        print(f"{parser.prog} {installed}")
        parser.exit()


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
    read: dict[Path, Status | None],
    written: set[Path],
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
        read: Dict to add the path of every template that the press reads to (see
            `Platen.loaded`), with its status from just before the press first read it.
        written: Set to add the path of every file that the press writes to (see
            `Platen.written`).

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

    templates_dir = template
    name: str | None = None

    if not is_dir:
        # The template isn't a directory, so it doesn't end with a separator, and its
        # name is never empty.
        directory, name = split(template)
        templates_dir = directory or "."

    def on_load(path: Path) -> None:
        # Keep the status from the first read. Jinja asks for a template again when it
        # couldn't load it, and reads one again when its modification time has changed,
        # but anything pressed before then used it as it was.
        if path not in read:
            read[path] = status(path)

    platen = Platen(
        templates_dir,
        values,
        on_load=on_load,
        protect=[
            values_path,
        ],
    )

    try:
        if name is None:
            return platen.press(output) > 0

        platen.press_file(name, output)
        return True

    finally:
        written.update(platen.written)


def _press_and_look(
    template: str,
    values_path: str,
    output: str,
    before: Look,
    prog: str,
) -> tuple[list[str], Look]:
    """
    Press while watching for changes, and find what the press depends on.

    Args:
        template: Path to the template, or directory of templates, to press.
        values_path: Path to the YAML file of values to press.
        output: Path to the file or directory to press into.
        before: The look taken just before the press, at every path that the last
            press read.
        prog: The program's name, to print.

    Returns:
        Paths to the templates that this press read, except any that it wrote to, and
        the look that it was pressed from.
    """
    found: dict[Path, Status | None] = {}
    written: set[Path] = set()

    _press_and_report(
        template,
        values_path,
        output,
        found,
        written,
        prog=prog,
        watching=True,
    )

    watched = unwritten(
        found,
        written,
    )

    return watched, baseline(
        before,
        {str(path): seen for path, seen in found.items()},
        look(template, values_path, watched),
    )


def _press_and_report(
    template: str,
    values_path: str,
    output: str,
    read: dict[Path, Status | None],
    written: set[Path],
    *,
    prog: str,
    watching: bool,
) -> int:
    """
    Press, and print what happened.

    Nothing is printed on success, except a warning when a directory has nothing to
    press, or a line that says what was pressed when `watching`. Any failure is printed
    as a single line, without a traceback.

    Args:
        template: Path to the template, or directory of templates, to press.
        values_path: Path to the YAML file of values to press.
        output: Path to the file or directory to press into.
        read: Dict to add the path of every template that the press read to, with its
            status from just before the press first read it.
        written: Set to add the path of every file that the press wrote to.
        prog: The program's name, to print.
        watching: Whether the press is one of many while watching for changes.

    Returns:
        0 on success, or 1 on failure.
    """
    try:
        pressed = _press(
            template,
            values_path,
            output,
            read,
            written,
        )

    except Exception as error:
        print(f"{prog}: error: {_describe(error)}", file=sys.stderr)
        return 1

    if not pressed:
        # Name the directory as it was given, rather than as the library logs it.
        print(
            f"{prog}: warning: Nothing to press in {template!r}: "
            "it's empty, or everything in it is ignored",
            file=sys.stderr,
        )

    elif watching:
        print(f"{prog}: Pressed {template!r} to {output!r}", file=sys.stderr)

    return 0


def _watch(
    template: str,
    values_path: str,
    output: str,
    prog: str,
) -> NoReturn:
    """
    Press, then press again whenever anything that the last press read changes, until
    interrupted.

    Events from watchfiles only wake Platen up. Whether to press again is decided by
    looking at everything that the last press read, and comparing it with how it was
    then. Platen's outputs aren't among what it read, so writing them never causes
    another press. And the paths that events name never have to be matched to what was
    read, which symlinks and case-insensitive file systems would make unreliable.
    They're only matched to the directories being watched, to watch one again that's
    replaced.

    Every press prints a line, and a press that fails doesn't stop the watch.

    Args:
        template: Path to the template, or directory of templates, to press.
        values_path: Path to the YAML file of values to press.
        output: Path to the file or directory to press into.
        prog: The program's name, to print.

    Raises:
        KeyboardInterrupt: When interrupted, which is how watching ends.
        _Error: When changes can't be watched.
    """
    # Importing watchfiles imports anyio too, which would slow down every press that
    # doesn't watch.
    from watchfiles import Change, watch

    read, pressed = _press_and_look(
        template,
        values_path,
        output,
        look(template, values_path, []),
        prog,
    )

    print(f"{prog}: Watching for changes. Press Ctrl+C to stop.", file=sys.stderr)

    # Whether the last watcher failed.
    failed = False

    # Whether Platen has warned that watchers keep failing, since one last woke.
    warned = False

    # The directories that the last watcher found had changed, so that the next needn't
    # find them again, or `None` when they must be found.
    rewatch: frozenset[str] | None = None

    while True:
        watching = (
            rewatch
            if rewatch is not None
            else _watched_directories(
                template,
                pressed,
            )
        )

        rewatch = None

        # Whether this watcher replaced one that failed, and hasn't woken yet.
        replaced_failed = failed
        failed = False

        # Every directory to watch is named, so none is watched recursively. Every event
        # is a reason to look, so none are filtered out. Changes that arrive together
        # wake the watcher once, after 50 milliseconds without another, for up to 1.6
        # seconds. The watcher wakes every 5 seconds without an event too, so that a
        # change that the operating system didn't report, like on a network drive, is
        # still seen.
        #
        # The options that watchfiles' environment variables could change are set, so
        # that it can't skip a directory that it can't watch, silently, or print its own
        # lines among Platen's. Only polling is left to them, which changes when Platen
        # wakes but never what it presses: `WATCHFILES_FORCE_POLLING` can make
        # watchfiles poll, like it does on WSL unless told not to, and
        # `WATCHFILES_POLL_DELAY_MS` says how often. Ctrl+C must raise
        # `KeyboardInterrupt`.
        with closing(
            watch(
                *sorted(watching),
                debounce=1_600,
                debug=False,
                ignore_permission_denied=False,
                raise_interrupt=True,
                recursive=False,
                rust_timeout=5_000,
                step=50,
                watch_filter=None,
                yield_on_timeout=True,
            ),
        ) as wakes:
            while True:
                # Look before waiting, so that a change made during a press, or before
                # this look, needn't wait for an event that's already passed. watchfiles
                # only starts watching at the first wake, after the look, so a change
                # made between them is seen at the first wake without an event instead,
                # within 5 seconds.
                if (now := look(template, values_path, read)) != pressed:
                    read, pressed = _press_and_look(
                        template,
                        values_path,
                        output,
                        now,
                        prog,
                    )

                    # Start a new watcher after every press. A press can read templates
                    # in other directories, and the change that caused it can have
                    # replaced a watched directory with another at the same path, like a
                    # checkout does. Linux stops watching a directory that's deleted,
                    # and the new one's number can be the same, so the directories to
                    # watch can look unchanged.
                    break

                try:
                    changes = next(wakes)

                except FileNotFoundError:
                    # watchfiles starts watching at the first wake, so a directory that
                    # was removed after it was found, like by a checkout, is missing by
                    # then. Find the directories again. A directory that's still found
                    # but that every new watcher can't find, like while a checkout
                    # keeps replacing it, mustn't start them as fast as they fail, so
                    # wait like for any other watcher that keeps failing.
                    if replaced_failed:
                        sleep(5)

                    failed = True
                    break

                except OSError as error:
                    raise _Error(f"Can't watch for changes: {error}") from error

                except RuntimeError as error:
                    # watchfiles keeps an error that an event raised, like one for a
                    # file whose name isn't UTF-8 on Linux, and raises it at every wake
                    # after. A new watcher starts without it, so start one. More events
                    # like it can fail that one too, before it wakes, so that mustn't
                    # end the watch. But nor must an error that every new watcher
                    # raises at once start them as fast as they fail, so when a watcher
                    # that replaced a failed one fails before it wakes, wait as long as
                    # a wake without an event would.
                    #
                    # Changes are still looked for that often, so say so, once, rather
                    # than let changes seem slow.
                    if replaced_failed:
                        if not warned:
                            print(
                                f"{prog}: warning: Can't watch for changes, so looking "
                                f"for them every 5 seconds instead: {error}",
                                file=sys.stderr,
                            )

                            warned = True

                        sleep(5)

                    failed = True
                    break

                replaced_failed = False
                warned = False

                # Start a new watcher when an event says that a watched directory was
                # deleted or added, because Linux stops watching a directory that's
                # deleted, even when another is made at the same path, like by a
                # checkout.
                #
                # An event that one was modified, like when polling sees what's in it
                # change, needs nothing.
                if any(
                    path in watching
                    for change, path in changes
                    if change is not Change.modified
                ):
                    break

                # Start one too when a directory appears within the templates directory
                # without a press. Only an event can say so: a wake without one is for a
                # change that wasn't reported, which the look sees.
                if (
                    changes
                    and (rewatch := _watched_directories(template, pressed)) != watching
                ):
                    break

                rewatch = None


def _watched_directories(template: str, pressed: Look) -> frozenset[str]:
    """
    Find the directories to watch for changes to what a press read.

    Args:
        template: Path to the template, or directory of templates, as it was given.
        pressed: The look that the last press was pressed from.

    Returns:
        The real path to each directory.

    Raises:
        _Error: When the working directory is gone, so relative paths lead nowhere.
    """
    try:
        return directories(
            template,
            pressed.statuses,
        )

    except FileNotFoundError as error:
        raise _Error(
            "The working directory no longer exists, so changes can't be watched"
        ) from error


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

    With `--watch`, it presses, then presses again whenever anything that the last
    press read changes, until it's interrupted. Every press prints a line, and a press
    that fails doesn't stop it.

    Args:
        argv: Command-line arguments, without the program's name. Defaults to the
            arguments that the program was run with.

    Returns:
        0 on success, 1 on failure, or 130 when interrupted, which is how watching
        ends.

    Raises:
        SystemExit: When the arguments are wrong (with 2), or help/version is asked for
            (with 0).
    """
    parser = ArgumentParser(
        # Prefixes like "--temp" are ambiguous between the template's two names, and
        # any prefix would break whenever a new option shared it.
        allow_abbrev=False,
        description="Press structured data into documents.",
        prog="platen",
    )

    parser.add_argument(
        "--version",
        action=_VersionAction,
        default=SUPPRESS,
        help="show program's version number and exit",
        nargs=0,
    )

    parser.add_argument(
        "--template",
        "--templates",
        help="path to the template or directory of templates to press",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--values",
        help="path to the YAML file of values to press into the templates",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--out",
        help="path to the file or directory to press into",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help="press again whenever the templates or values change, until interrupted",
    )

    arguments = parser.parse_args(argv)

    try:
        if arguments.watch:
            _watch(
                arguments.template,
                arguments.values,
                arguments.out,
                parser.prog,
            )

        return _press_and_report(
            arguments.template,
            arguments.values,
            arguments.out,
            {},
            set(),
            prog=parser.prog,
            watching=False,
        )

    except KeyboardInterrupt:
        # Stop quietly, with the exit code that shells give an interrupted command.
        return 130
    except Exception as error:
        # Every press reports its own failure, so only watching for changes fails here.
        print(f"{parser.prog}: error: {_describe(error)}", file=sys.stderr)
        return 1
