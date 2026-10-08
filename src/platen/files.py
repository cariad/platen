from errno import EEXIST, EISDIR, ENOENT, ENOTDIR
from os import PathLike, fspath, stat, strerror
from os.path import realpath
from pathlib import Path
from re import search
from typing import TypeVar

from .types import LineEnding, Sniff

SNIFF_LENGTH = 8_000
"""
Number of bytes to sniff to learn about a file.

This must remain at least 8,000. Like Git, we'll assume a file is binary
if it contains a NUL (b"\0") character in the first 8,000 bytes.
"""

_E = TypeVar("_E", bound=OSError)

_ERRNOS: dict[type[OSError], int] = {
    FileExistsError: EEXIST,
    FileNotFoundError: ENOENT,
    IsADirectoryError: EISDIR,
    NotADirectoryError: ENOTDIR,
}
"""The error number of each kind of `OSError` that Platen raises itself."""

_LINE_ENDING_PATTERN = b"|".join(
    e.encode() for e in sorted(LineEnding, key=len, reverse=True)
)
"""
Matches any line ending.

Longer sequences are tried first so that a CRLF is not matched as a CR.
"""


def _identity(path: Path) -> tuple[int, int] | None:
    """
    Identify the file at a path that's already been resolved with `realpath`.

    Args:
        path: Resolved path to the file to identify.

    Returns:
        The file's device and inode numbers, or `None` when nothing exists at `path`.

    Raises:
        OSError: When the file can't be identified for any other reason, like
            permissions or a loop of symlinks.
    """
    try:
        st = stat(path)
    except (FileNotFoundError, NotADirectoryError):
        # `NotADirectoryError` means that one of the path's parents is a file, so
        # nothing can exist at the path.
        return None

    return st.st_dev, st.st_ino


def identity(path: Path) -> tuple[int, int] | None:
    """
    Identify the file at `path`, following symlinks.

    Two paths have the same identity when they name the same file. That's true of a
    symlink or a hard link to the file, and of a spelling that a case- or
    normalisation-insensitive file system treats as the same name, like `README.md` and
    `readme.md` on macOS's default APFS.

    Comparing paths as strings can't see any of those, and `Path.resolve()` keeps the
    caller's case, so we compare identities instead.

    The path is resolved before it's identified, so a ".." after a directory that
    doesn't exist yet steps up the same way it will once the directory is created. For
    example, "missing/../doc.md" is identified as "doc.md".

    Args:
        path: Path to the file to identify.

    Returns:
        The file's device and inode numbers, or `None` when nothing exists at `path`.

    Raises:
        OSError: When the file can't be identified for any other reason, like
            permissions or a loop of symlinks. A safety check that can't be made must
            fail rather than pass.
    """
    return _identity(Path(realpath(path)))


def is_resolved_within(resolved: Path, directory: tuple[int, int] | None) -> bool:
    """
    Check whether a resolved path is a directory or within it.

    `resolved` must already be resolved with `realpath`, so that it and each of its
    parents can be identified directly, without resolving each of them again.

    Args:
        resolved: Resolved path to check.
        directory: The directory's identity, from `identity`, or `None` when the
            directory doesn't exist.

    Returns:
        `True` if `resolved` is the directory or within it, otherwise `False`. Always
        `False` when the directory doesn't exist.

    Raises:
        OSError: When a file along the path can't be identified, like when permissions
            deny it or symlinks loop.
    """
    if directory is None:
        # Nothing can be within a directory that doesn't exist, and the paths that
        # don't exist have no identity either, so they mustn't be compared with it.
        return False

    return any(_identity(p) == directory for p in (resolved, *resolved.parents))


def is_within(path: Path, directory: Path) -> bool:
    """
    Check whether `path` is `directory` or within it, wherever symlinks lead.

    `path` is resolved first, so a symlink anywhere along it, even a dangling one at its
    end, is followed. Then the resolved path and each of its parents are compared with
    `directory` by identity, so that a spelling like `TEMPLATES/build` is still within
    `templates` on a case-insensitive file system. A path that doesn't exist yet is
    judged by its nearest existing parents.

    Args:
        path: Path to check.
        directory: Path to the directory.

    Returns:
        `True` if `path` is `directory` or within it, otherwise `False`. Always `False`
        when `directory` doesn't exist.

    Raises:
        OSError: When a file along the resolved path can't be identified, like when
            permissions deny it or symlinks loop.
    """
    # `Path.resolve()` raises `RuntimeError` for a loop of symlinks on Python 3.11 and
    # 3.12, but `realpath` never raises, so a loop fails the same way on every version:
    # with `OSError` when the looping path is identified.
    return is_resolved_within(Path(realpath(path)), identity(directory))


def os_error(
    error: type[_E],
    path: PathLike[str] | str,
    other: PathLike[str] | str | None = None,
    reason: str | None = None,
) -> _E:
    """
    Make an `OSError` like the one the operating system would raise.

    The error number and message come from the type of error, so they always agree.

    Args:
        error: Type of error to make.
        path: Path that the error is about.
        other: Another path that the error is about, if there is one.
        reason: Why the error was raised, to add to the message, if it needs saying.

    Returns:
        The error.
    """
    code = _ERRNOS[error]
    message = strerror(code) if reason is None else f"{strerror(code)} ({reason})"
    filename2 = None if other is None else fspath(other)
    return error(code, message, fspath(path), None, filename2)


def sniff(path: Path) -> Sniff:
    """
    Sniff the file at `path` to discover whether it is text and, if so,
    which line ending it uses.

    Args:
        path: Path to the file to sniff.

    Returns:
        Facts about the file.
    """
    with path.open("rb") as f:
        head = f.read(SNIFF_LENGTH)

        if b"\0" in head:
            # Like Git, we assume a file is binary if it contains a NUL
            # (b"\0") character in the first 8,000 bytes.
            return Sniff(is_text=False)

        match = search(_LINE_ENDING_PATTERN, head)

        if match is None:
            return Sniff(
                is_text=True,
                line_ending=None,
            )

        line_ending = LineEnding(match.group().decode())

        # A CRLF might straddle the end of the head, so peek at the next
        # byte before concluding that this is a lone CR.
        if (
            line_ending is LineEnding.CR
            and match.end() == len(head)
            and f.read(1) == LineEnding.LF.encode()
        ):
            line_ending = LineEnding.CRLF

    return Sniff(
        is_text=True,
        line_ending=line_ending,
    )
