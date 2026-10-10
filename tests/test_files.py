from errno import ELOOP
from os import link
from os.path import exists, realpath
from pathlib import Path

from pytest import MonkeyPatch, mark, raises, skip

from platen.files import identity, is_within


@mark.parametrize(
    ("path", "expect_same"),
    [
        ("file.md", True),
        ("link.md", True),
        ("hard.md", True),
        ("FILE.md", True),
        ("missing/../file.md", True),
        ("other.md", False),
    ],
    ids=[
        "same path",
        "symlink",
        "hard link",
        "different case",
        "via parent of missing directory",
        "different file",
    ],
)
def test_identity(path: str, expect_same: bool, tmp_path: Path) -> None:
    """
    Assert that `identity` is the same for every name of a file, and different for
    different files.
    """
    (tmp_path / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "link.md").symlink_to("file.md")
    link(tmp_path / "file.md", tmp_path / "hard.md")

    # A different case only names the same file on case-insensitive file systems, like
    # macOS's default APFS.
    if not exists(realpath(tmp_path / path)):
        skip(f"'{path}' names a different file on this file system")

    same = identity(tmp_path / path) == identity(tmp_path / "file.md")
    assert same is expect_same


@mark.parametrize(
    "path",
    [
        "missing.md",
        "file.md/child.md",
    ],
    ids=[
        "missing",
        "parent is a file",
    ],
)
def test_identity_is_none_when_nothing_exists(path: str, tmp_path: Path) -> None:
    """Assert that `identity` is `None` when nothing exists at the path."""
    (tmp_path / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    assert identity(tmp_path / path) is None


def test_identity_names_path_as_given(tmp_path: Path) -> None:
    """
    Assert that `identity` names the path as it was given, rather than where it leads,
    when it can't identify the file.
    """
    (tmp_path / "loop").symlink_to("loop")
    (tmp_path / "alias").symlink_to("loop")

    with raises(OSError) as ex:
        identity(tmp_path / "alias")

    assert ex.value.errno == ELOOP
    assert ex.value.filename == str(tmp_path / "alias")


def test_identity_raises_when_working_directory_is_gone(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Assert that `identity` raises for a relative path when the working directory is
    gone, rather than saying that nothing exists there. A safety check that can't be
    made must fail rather than pass.
    """
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    working.rmdir()

    with raises(FileNotFoundError):
        identity(Path("doc.md"))


@mark.parametrize(
    ("path", "expect"),
    [
        ("dir", True),
        ("dir/sub", True),
        ("dir/sub/deep", True),
        ("dir/missing/file.md", True),
        ("link/file.md", True),
        ("dangling", True),
        ("dir/out/file.md", False),
        ("sibling", False),
        ("missing/file.md", False),
    ],
    ids=[
        "directory itself",
        "within",
        "deeper within",
        "missing within",
        "through symlink",
        "through dangling symlink",
        "through symlink leading out",
        "sibling",
        "missing elsewhere",
    ],
)
def test_is_within(path: str, expect: bool, tmp_path: Path) -> None:
    """
    Assert that `is_within` is `True` when the path is the directory or within it,
    wherever symlinks lead, and whether or not the path exists.

    A path that's named within the directory but leads out of it through a symlink
    isn't within it.
    """
    (tmp_path / "dir" / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "sibling").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "dir" / "sub", target_is_directory=True)
    (tmp_path / "dangling").symlink_to(tmp_path / "dir" / "new.md")
    (tmp_path / "dir" / "out").symlink_to(
        tmp_path / "sibling", target_is_directory=True
    )

    assert is_within(tmp_path / path, tmp_path / "dir") is expect


def test_is_within_by_different_case(tmp_path: Path) -> None:
    """
    Assert that `is_within` is `True` when the path names the directory in a different
    case, on a case-insensitive file system like macOS's default APFS.
    """
    (tmp_path / "dir").mkdir()

    if not (tmp_path / "DIR").exists():
        skip("'DIR' names a different directory on this file system")

    assert is_within(tmp_path / "DIR" / "file.md", tmp_path / "dir")


def test_is_within_is_false_when_directory_is_missing(tmp_path: Path) -> None:
    """
    Assert that `is_within` is `False` when the directory doesn't exist, even for a path
    within it that doesn't exist either.
    """
    assert not is_within(tmp_path / "missing" / "file.md", tmp_path / "missing")


def test_is_within_raises_for_symlink_loop(tmp_path: Path) -> None:
    """
    Assert that `is_within` raises `OSError` when symlinks loop, naming the path as it
    was given rather than where it leads.
    """
    (tmp_path / "loop").symlink_to("loop")
    path = tmp_path / "loop" / "file.md"

    with raises(OSError) as ex:
        is_within(
            path,
            tmp_path,
        )

    assert ex.value.errno == ELOOP
    assert ex.value.filename == str(path)
