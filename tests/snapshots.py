from pathlib import Path
from stat import S_IMODE


def snapshot(directory: Path) -> dict[Path, tuple[bytes, int] | None]:
    """
    Snapshot everything within a directory, so that a test can assert that nothing
    changed: every path, and the body and mode of every file.
    """
    return {
        p.relative_to(directory): (
            (p.read_bytes(), S_IMODE(p.stat().st_mode)) if p.is_file() else None
        )
        for p in directory.rglob("*")
    }
