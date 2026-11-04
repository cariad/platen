from os import utime
from pathlib import Path

_SECOND = 1_000_000_000
"""One second, in nanoseconds."""


def edit(path: Path, body: str) -> None:
    """
    Rewrite a file, and set its modification time to a second after what it was, so
    that a look always sees the edit, even at the same size on a file system whose
    timestamps are too coarse to tell two writes apart.
    """
    modified = path.stat().st_mtime_ns + _SECOND
    path.write_text(body, encoding="utf-8")
    utime(path, ns=(modified, modified))
