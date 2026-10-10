from os import PathLike, fspath, stat
from os.path import realpath
from pathlib import Path


def _identity(
    resolved: PathLike[str] | str,
    given: PathLike[str] | str,
) -> tuple[int, int] | None:
    """
    Identify the file at a path that's already been resolved with `realpath`.

    Args:
        resolved: Resolved path to the file to identify.
        given: Path to name in an error, as it was given.

    Returns:
        The file's device and inode numbers, or `None` when nothing exists at
        `resolved`.

    Raises:
        OSError: When the file can't be identified for any other reason, like
            permissions or a loop of symlinks. The error names `given`.
    """
    try:
        st = stat(resolved)
    except (FileNotFoundError, NotADirectoryError):
        # `NotADirectoryError` means that one of the path's parents is a file, so
        # nothing can exist at the path.
        return None
    except OSError as error:
        # Name the path as it was given, rather than where it leads.
        raise OSError(
            error.errno,
            error.strerror,
            fspath(given),
        ) from error

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
            fail rather than pass. The error names `path` as it was given.
    """
    # `realpath` raises, rather than passing as a file that doesn't exist, for a
    # relative path in a working directory that's gone.
    return _identity(
        realpath(path),
        path,
    )


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
            permissions deny it or symlinks loop. The error names `path` as it was
            given.
    """
    if (found := identity(directory)) is None:
        # Nothing can be within a directory that doesn't exist, and the paths that
        # don't exist have no identity either, so they mustn't be compared with it.
        return False

    # `Path.resolve()` raises `RuntimeError` for a loop of symlinks on Python 3.11 and
    # 3.12, but `realpath` never raises, so a loop fails the same way on every version:
    # with `OSError` when the looping path is identified.
    # The path and its parents are already resolved, so identify them without resolving
    # each of them again.
    resolved = Path(realpath(path))
    return any(_identity(p, path) == found for p in (resolved, *resolved.parents))
