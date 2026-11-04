from os import PathLike, stat
from stat import S_ISREG

from .types import Status


def status(path: PathLike[str] | str) -> Status | None:
    """
    Look at the file at `path`, following every symlink it contains, without reading it.

    Args:
        path: Path to the file.

    Returns:
        The file's status, or `None` when it can't be found.
    """
    try:
        result = stat(path)

    except OSError, ValueError:
        # Nothing's there, a symlink is broken or loops, permissions hide the file, or
        # the path holds a null character, like a template that's named from values.
        # A press can't read any of those, so they can only change by appearing.
        return None

    if not S_ISREG(result.st_mode):
        return Status(
            changed=None,
            device=result.st_dev,
            inode=result.st_ino,
            mode=result.st_mode,
            modified=None,
            size=None,
        )

    return Status(
        changed=result.st_ctime_ns,
        device=result.st_dev,
        inode=result.st_ino,
        mode=result.st_mode,
        modified=result.st_mtime_ns,
        size=result.st_size,
    )
