from collections.abc import Callable, Mapping
from dataclasses import dataclass
from os import stat
from pathlib import Path
from stat import S_ISDIR

from rootle import fingerprint
from rootle.types import Fingerprint

from ..platen import Platen
from ..split import split_template
from .console import error, say, warn
from .describe import describe
from .values import load_values


def _press(
    target: Target,
    on_load: Callable[[Path], None],
    written: set[Path],
) -> int:
    """
    Press, and add the path of every file that the press writes to `written`, even when
    the press fails.

    Args:
        target: What to press, and where.
        on_load: Passed to `Platen`, to be called with the path to every template that
            the press reads, just before it's read.
        written: Set to add the paths to.

    Returns:
        The number of templates pressed, which is 0 when there was nothing to press.

    Raises:
        Exception: When anything stops the press. The templates pressed before it
            stopped stay pressed.
    """
    # Check the template as it was given, before reading any values, so that the error
    # names it that way, and so that a path like "doc.md/" is refused rather than tidied
    # up.
    is_dir = S_ISDIR(stat(target.template).st_mode)

    values = load_values(target.values)

    if is_dir:
        templates_dir, name = target.template, None
    else:
        templates_dir, name = split_template(target.template)

    platen = Platen(
        templates_dir,
        values,
        on_load=on_load,
        protect=[
            target.values,
        ],
    )

    try:
        if name is None:
            return platen.press(target.out)

        platen.press_file(name, target.out)
        return 1

    finally:
        written.update(platen.written)


@dataclass(frozen=True, slots=True)
class Pressed:
    """What a press read and wrote, and how it ended."""

    count: int
    """
    The number of templates that the press pressed, which is 0 when a directory had
    nothing to press, or when the press failed.
    """

    error: Exception | None
    """
    What stopped the press, or `None` when nothing did. The templates pressed before it
    stopped stay pressed.
    """

    read: Mapping[Path, Fingerprint | None]
    """
    The path of every template that the press read (see `Platen.loaded`), with its
    fingerprint from just before the press first read it.
    """

    written: frozenset[Path]
    """The path of every file that the press wrote to (see `Platen.written`)."""


@dataclass(frozen=True, slots=True)
class Target:
    """What to press, and where, as given on the command line."""

    template: str
    """Path to the template, or directory of templates, to press."""

    values: str
    """Path to the YAML file of values to press."""

    out: str
    """
    Path to the file to press the template to, or the directory to press every template
    into.
    """


def press(target: Target) -> Pressed:
    """
    Press values from a YAML file into a template, or into every template within a
    directory.

    A template's own directory is the source templates directory, so that the templates
    that it references are found relative to it. The values file is protected from
    being pressed over.

    Nothing is raised when the press fails, so that what it read and wrote before it
    failed is still known: see `Pressed.error`.

    Args:
        target: What to press, and where.

    Returns:
        What the press read and wrote, and how it ended.
    """
    read: dict[Path, Fingerprint | None] = {}
    written: set[Path] = set()

    def on_load(path: Path) -> None:
        # Keep the fingerprint from the first read. Jinja asks for a template again when
        # it couldn't load it, and reads one again when its modification time has
        # changed, but anything pressed before then used it as it was.
        if path not in read:
            read[path] = fingerprint(path)

    try:
        count = _press(target, on_load, written)

    except Exception as ex:
        return Pressed(
            count=0,
            error=ex,
            read=read,
            written=frozenset(written),
        )

    return Pressed(
        count=count,
        error=None,
        read=read,
        written=frozenset(written),
    )


def report(target: Target, pressed: Pressed, *, watching: bool) -> int:
    """
    Print what happened in a press.

    Nothing is printed on success, except a warning when a directory has nothing to
    press, or a line that says what was pressed when `watching`. Any failure is printed
    as a single line, without a traceback.

    Args:
        target: What was pressed, and where.
        pressed: How the press went.
        watching: Whether the press is one of many while watching for changes.

    Returns:
        0 on success, or 1 on failure.
    """
    if pressed.error is not None:
        error(describe(pressed.error))
        return 1

    if pressed.count == 0:
        # Name the directory as it was given, rather than as the library logs it.
        warn(
            f"Nothing to press in {target.template!r}: "
            "it's empty, or everything in it is ignored"
        )

    elif watching:
        say(f"Pressed {target.template!r} to {target.out!r}")

    return 0
