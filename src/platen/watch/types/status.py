from typing import NamedTuple


class Status(NamedTuple):
    """
    What a look saw of a file, through any symlinks, without reading it.

    Compare statuses with `==`. A file has changed when its status has.

    The file's access time is left out, because every press reads its templates. Its
    change and modification times and its size are `None` for anything but a regular
    file, because only a regular file's content is pressed: a directory's times change
    whenever any entry within it does, even an ignored one, and a terminal's change as
    someone types.
    """

    changed: int | None
    """
    The file's status change time in nanoseconds (`st_ctime_ns`), which catches writes
    by tools that put the modification time back, like `cp -p` or `touch -r`.
    """

    device: int
    """The device number of the file system that holds the file."""

    inode: int
    """
    The file's number within its file system. A new number, with `device`, means that
    the path names another file, like after an atomic save or a symlink retargeted.
    """

    mode: int
    """The file's type and permissions, which a press copies to its output."""

    modified: int | None
    """The file's modification time in nanoseconds (`st_mtime_ns`)."""

    size: int | None
    """The file's size in bytes."""
