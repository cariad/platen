from os import stat, utime
from pathlib import Path

from pytest import mark, skip
from rootle.testing import can_chmod_deny_search

from platen.watch import Status, status


@mark.parametrize(
    "path",
    [
        "real/link.md",
        # A symlink to `link.md`, so two symlinks are followed to reach `file.md`.
        "real/chain.md",
        # A file within a symlink to `real`, so the symlink is partway along the path
        # rather than at its end.
        "linked/file.md",
    ],
)
def test_status_follows_symlinks(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `status` must give the status of the file that a path leads to through every
    symlink within it, rather than of a symlink, so that retargeting one is seen.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "link.md").symlink_to("file.md")
    (real_dir / "chain.md").symlink_to("link.md")
    (tmp_path / "linked").symlink_to(real_dir, target_is_directory=True)

    expect = status(real_dir / "file.md")
    result = status(tmp_path / path)

    assert expect is not None
    assert result == expect


def test_status_gives_every_field_of_regular_file(tmp_path: Path) -> None:
    """
    `status` must give a regular file's identity, mode, size, and change and
    modification times, whether its path is a `Path` or a `str`.
    """
    path = tmp_path / "file.md"
    path.write_text("Hello, world!\n", encoding="utf-8")

    # Move the modification time away from the change time, so that they can't be
    # mistaken for each other.
    utime(path, ns=(0, 1_000_000_000))
    found = stat(path)
    assert found.st_ctime_ns != found.st_mtime_ns

    expect = Status(
        changed=found.st_ctime_ns,
        device=found.st_dev,
        inode=found.st_ino,
        mode=found.st_mode,
        modified=found.st_mtime_ns,
        size=14,
    )
    result_from_path = status(path)
    result_from_str = status(str(path))

    assert result_from_path == expect
    assert result_from_str == expect


@mark.parametrize(
    "path",
    [
        "directory",
        "/dev/null",
    ],
)
def test_status_gives_identity_and_mode_only_of_file_that_is_not_regular(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `status` must give only the identity and mode of anything but a regular file,
    leaving its times and size `None`, because a directory's times change whenever any
    entry within it does and a device's change as it's used.
    """
    (tmp_path / "directory").mkdir()

    # An absolute path replaces `tmp_path` when they're joined.
    target = tmp_path / path

    if not target.exists():
        skip(f"This platform doesn't have {path}")

    found = stat(target)

    result = status(target)
    expect = Status(
        changed=None,
        device=found.st_dev,
        inode=found.st_ino,
        mode=found.st_mode,
        modified=None,
        size=None,
    )

    assert result == expect


@mark.parametrize(
    "path",
    [
        "missing.md",
        "missing/file.md",
        # A path that treats a regular file as a directory, which raises
        # `NotADirectoryError` rather than `FileNotFoundError`.
        "file.md/file.md",
        # A symlink to a file that doesn't exist.
        "broken.md",
        # A symlink that leads back to itself, so nothing can be found there.
        "loop.md",
        # A null character, which raises `ValueError` rather than `OSError`.
        "null\0.md",
    ],
)
def test_status_is_none_when_file_cannot_be_found(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `status` must give `None`, rather than raise, for a path that leads to nothing that
    a press could read, like one that's missing, a broken symlink, a loop of symlinks,
    or a template named from values with a null character.
    """
    (tmp_path / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "broken.md").symlink_to("nowhere.md")
    (tmp_path / "loop.md").symlink_to("loop.md")

    result = status(tmp_path / path)

    assert result is None


def test_status_is_none_when_permissions_hide_file(tmp_path: Path) -> None:
    """
    `status` must give `None`, rather than raise, for a file within a directory that
    can't be searched, because a press can't read it either.
    """
    if not can_chmod_deny_search(tmp_path):
        skip("Permissions don't stop this user from searching directories")

    directory = tmp_path / "locked"
    directory.mkdir()
    (directory / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    directory.chmod(0o600)

    try:
        result = status(directory / "file.md")

        assert result is None

    finally:
        directory.chmod(0o755)
