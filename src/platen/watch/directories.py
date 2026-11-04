from collections.abc import Iterable
from os import R_OK, X_OK, access, scandir, walk
from os.path import abspath, dirname, isdir, islink, join, realpath
from pathlib import Path


def _holds_only_directories(path: str) -> bool:
    """
    Tell whether the directory at `path` holds nothing but directories, if anything.

    Args:
        path: Path to the directory.

    Returns:
        `True` when the directory can be listed and holds nothing that isn't a
        directory. A symlink isn't a directory, because walks never step into one.
    """
    try:
        with scandir(path) as entries:
            return all(entry.is_dir(follow_symlinks=False) for entry in entries)
    except OSError:
        return False


def _nearest_directory(path: str) -> str:
    """
    Find the nearest directory to `path` that can be watched.

    Args:
        path: Absolute path to the directory to watch.

    Returns:
        `path`, or else its nearest ancestor that can be watched. The search ends at
        the root directory, even if that can't be watched, so that watching it fails
        rather than the search never ending.
    """
    while not _watchable(path) and (parent := dirname(path)) != path:
        path = parent

    return path


def _watchable(path: str) -> bool:
    """
    Tell whether the directory at `path` can be watched.

    Args:
        path: Absolute path to the directory.

    Returns:
        `True` when the directory exists, can be listed, and has a name that watchfiles
        can take.
    """
    try:
        # Linux can name a file with bytes that aren't UTF-8, which Python escapes, but
        # watchfiles only takes UTF-8 names.
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False

    # inotify can't watch a directory that can't be read. Watching its parent still
    # sees its permissions change.
    return isdir(path) and access(path, R_OK | X_OK)


def directories(template: str, paths: Iterable[str]) -> frozenset[str]:
    """
    Find the directories to watch, non-recursively, to see a change to any of `paths`.

    Directories are watched rather than files, because an atomic save, or deleting a
    file and creating it again, replaces the file, and Linux stops watching a file
    that's replaced. Watching directories also sees files appear where there were none.

    A directory that's missing, or can't be watched, is replaced by its nearest ancestor
    that can be, so that a change to it is still seen.

    Args:
        template: Path to the template, or directory of templates, as it was given.
        paths: Path to every file to watch, including `template`.

    Returns:
        The real path to each directory.

    Raises:
        FileNotFoundError: When a path is relative but the working directory is gone.
    """
    found: set[str] = set()

    # The real path to every directory that holds a path to watch, or holds one that
    # does.
    leading: set[str] = set()

    for path in paths:
        absolute = abspath(path)

        try:
            # Watch the directory that holds the path's last part, and the directory
            # that the path leads into through any symlinks, so that a change to either
            # is seen.
            listed = realpath(dirname(absolute))
            leads = dirname(realpath(absolute))

        except ValueError:
            # The path holds a null character, so nothing can ever be found there.
            continue

        found.add(_nearest_directory(listed))
        found.add(_nearest_directory(leads))

        leading.update(
            (
                listed,
                *map(str, Path(listed).parents),
            ),
        )

        # A directory along the path can be a symlink, and retargeting it changes where
        # the path leads, so watch the directory that holds each one too.
        for parent in Path(absolute).parents:
            if islink(parent):
                found.add(_nearest_directory(realpath(parent.parent)))

    if isdir(template):
        # Watch every directory within the templates directory that leads to a path to
        # watch, or that holds nothing but directories, if anything, so that a template
        # created in a new directory, even a nested one, is seen at once.
        #
        # Don't walk into any other: every file in it is ignored, or isn't a template,
        # like an image, and it can hold more directories than can be watched, like
        # `node_modules` or `.git`.
        #
        # A template created in one is seen at the next wake without an event instead,
        # even when the only files in it are ignored, like an editor's swap file or
        # macOS's `.DS_Store`.
        #
        # Like a press, the walk never steps into symlinks. It skips directories that
        # can't be read.
        for directory, names, _ in walk(template):
            found.add(_nearest_directory(realpath(directory)))

            names[:] = [
                name
                for name in names
                if realpath(join(directory, name)) in leading
                or _holds_only_directories(join(directory, name))
            ]

    return frozenset(found)
