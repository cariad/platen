from collections.abc import Iterable
from pathlib import Path

from rootle import identity


def unwritten(read: Iterable[Path], destinations: Iterable[Path]) -> list[str]:
    """
    Leave out the templates that a press wrote to.

    A template can include its own output, and so can a template that a symlink within
    a directory's output leads back to. Watching one would make every press cause
    another, forever.

    Files are compared by identity rather than by path, so that symlinks, hard links
    and case-insensitive file systems can't hide them.

    Args:
        read: Paths to the templates that the press read.
        destinations: Paths that the press wrote to.

    Returns:
        Each path in `read` that isn't the same file as a destination, sorted. A path
        that can't be found is kept, so that its creation is seen.
    """
    # A path that can't be found or read, like a missing one, a broken or looping
    # symlink, or one that permissions hide, has no identity. A press can't read or
    # write any of those.
    written = {
        found
        for path in destinations
        if (found := identity(path, strict=False)) is not None
    }

    return sorted(
        str(path)
        for path in read
        if (found := identity(path, strict=False)) is None or found not in written
    )
