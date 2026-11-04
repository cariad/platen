from collections.abc import Callable
from importlib import import_module
from os.path import realpath
from pathlib import Path

from pytest import MonkeyPatch, mark, raises, skip
from rootle.testing import can_chmod_deny_listing, can_chmod_deny_search

from platen.watch import directories

# `platen.watch` re-exports the function under the module's own name.
_module = import_module("platen.watch.directories")


def _always(*_: object) -> bool:
    """Say yes, whatever's asked."""
    return True


def _never(*_: object) -> bool:
    """Say no, whatever's asked."""
    return False


def _real(*paths: Path) -> frozenset[str]:
    """Find the real path to each of `paths`, the way `directories` names them."""
    return frozenset(realpath(p) for p in paths)


@mark.parametrize(
    ("mode", "can_chmod_deny"),
    [
        # Write and search without read, so the directory can't be listed.
        (0o300, can_chmod_deny_listing),
        # Read and write without search, so nothing within the directory can be
        # reached.
        (0o600, can_chmod_deny_search),
    ],
)
def test_directories_climbs_from_directory_that_cannot_be_read(
    mode: int,
    can_chmod_deny: Callable[[Path], bool],
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the parent of a directory that permissions stop it from
    listing or searching, because the directory itself can't be watched, but its parent
    sees its permissions change.
    """
    if not can_chmod_deny(tmp_path):
        skip("Permissions don't stop this user from reading directories")

    directory = tmp_path / "locked"
    directory.mkdir()
    template = directory / "doc.md"
    template.write_text("Hello\n", encoding="utf-8")
    directory.chmod(mode)

    try:
        found = directories(str(template), [str(template)])
    finally:
        directory.chmod(0o755)

    expect = _real(tmp_path)

    assert found == expect


def test_directories_climbs_from_name_that_is_not_utf8(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the parent of a directory whose name isn't UTF-8, which
    Python escapes with surrogates, because watchfiles only takes UTF-8 names.
    """
    # macOS refuses such a name, so pretend that every directory exists and can be
    # read, and only the name can stop one being watched.
    monkeypatch.setattr(_module, "access", _always)
    monkeypatch.setattr(_module, "isdir", _always)

    template = str(tmp_path / "caf\udce9" / "doc.md")

    result = directories(template, [template])
    expect = _real(tmp_path)

    assert result == expect


@mark.parametrize(
    "path",
    [
        # Only the file is missing, so the directory that holds it is the nearest.
        "sub/missing.md",
        "sub/gone/missing.md",
        "sub/gone/deeper/deepest/missing.md",
    ],
)
def test_directories_climbs_to_nearest_ancestor_of_missing_directory(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the nearest directory that exists above a missing path,
    however many of the directories along it are missing, so that the path appearing
    is seen.
    """
    (tmp_path / "sub").mkdir()
    template = str(tmp_path / path)

    result = directories(template, [template])
    expect = _real(tmp_path / "sub")

    assert result == expect


def test_directories_finds_directories_of_symlink_and_its_target(
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the directory that holds a symlink, which sees it
    retargeted, and the directory that it leads into, which sees its target change.
    """
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "project").mkdir()
    target = tmp_path / "elsewhere" / "README.template"
    target.write_text("Hello\n", encoding="utf-8")
    link = tmp_path / "project" / "README.template"
    link.symlink_to("../elsewhere/README.template")

    result = directories(str(link), [str(link)])
    expect = _real(
        tmp_path / "elsewhere",
        tmp_path / "project",
    )

    assert result == expect


def test_directories_finds_directory_that_holds_symlink_along_path(
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the directory that holds a symlink partway along a path,
    as well as where the path leads, because retargeting the symlink, like a deploy
    does, changes which file the path names.
    """
    (tmp_path / "v1").mkdir()
    (tmp_path / "current").symlink_to("v1")
    template = str(tmp_path / "current" / "README.template")

    result = directories(template, [template])
    expect = _real(tmp_path, tmp_path / "v1")

    assert result == expect


def test_directories_finds_every_directory_that_leads_to_path_named_relatively(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must watch every directory within a templates directory that leads to
    a path to watch, even when the templates directory and the paths are named relative
    to the working directory, like on the command line.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "a" / "b").mkdir(parents=True)
    (templates_dir / "a" / "b" / "page.md").write_text("Page\n", encoding="utf-8")

    # A file, so that `a` is walked into only because it leads to `page.md`.
    (templates_dir / "a" / "notes.log").write_text("Notes\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    found = directories(
        "templates",
        [
            "templates",
            "templates/a/b/page.md",
        ],
    )
    expect = _real(
        tmp_path,
        templates_dir,
        templates_dir / "a",
        templates_dir / "a" / "b",
    )

    assert found == expect


def test_directories_finds_every_directory_that_leads_to_path_or_holds_only_directories(
    tmp_path: Path,
) -> None:
    """
    `directories` must watch every directory within a templates directory that holds a
    path to watch, or holds one that does, or holds nothing but directories, if
    anything, even an ignored one, so that a template created in a new directory, even
    a nested one, is seen at once. It must never walk into any other directory, which
    can hold more directories than can be watched, like `node_modules`, nor step into a
    symlink to a directory, like a press.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "ignored").mkdir(parents=True)
    (templates_dir / "new" / "nested").mkdir(parents=True)
    (templates_dir / "sub" / "deep").mkdir(parents=True)
    (templates_dir / "sub" / "deep" / "page.md").write_text("Page\n", encoding="utf-8")
    (templates_dir / ".platenignore").write_text("ignored/\n", encoding="utf-8")
    (templates_dir / "node_modules" / "pkg").mkdir(parents=True)
    (templates_dir / "node_modules" / "pkg" / "index.js").write_bytes(b"")

    # npm writes this file into `node_modules`, so it's not walked into.
    (templates_dir / "node_modules" / ".package-lock.json").write_bytes(b"{}")

    (tmp_path / "elsewhere" / "inner").mkdir(parents=True)

    (templates_dir / "linked").symlink_to(
        tmp_path / "elsewhere",
        target_is_directory=True,
    )

    found = directories(
        str(templates_dir),
        [
            str(templates_dir),
            str(templates_dir / "sub" / "deep" / "page.md"),
        ],
    )
    expect = _real(
        tmp_path,
        templates_dir,
        templates_dir / "ignored",
        templates_dir / "new",
        templates_dir / "new" / "nested",
        templates_dir / "sub",
        templates_dir / "sub" / "deep",
    )

    assert found == expect


@mark.parametrize(
    ("working_dir", "template"),
    [
        ("project", "templates"),
        # A trailing separator, so the last part of the path is empty rather than
        # `templates`.
        ("project", "templates/"),
        # A path that steps into `sub` and back out again, so its last part is `..`.
        ("project", "templates/sub/.."),
        # The templates directory is the working directory, so the path names no
        # directory that holds it.
        ("project/templates", "."),
    ],
)
def test_directories_finds_same_directories_for_any_spelling(
    working_dir: str,
    template: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the same directories for a templates directory however
    it's spelled relative to the working directory: the directory that holds it, and
    every directory within it that holds nothing but directories, if anything.
    """
    project = tmp_path / "project"
    (project / "templates" / "sub").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / working_dir)

    result = directories(template, [template])
    expect = _real(
        project,
        project / "templates",
        project / "templates" / "sub",
    )

    assert result == expect


def test_directories_finds_working_directory_for_name_without_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must watch the working directory for a relative path that names no
    directory, like `README.template`, rather than an empty path.
    """
    (tmp_path / "README.template").write_text("Hello\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = directories("README.template", ["README.template"])
    expect = _real(tmp_path)

    assert result == expect


def test_directories_raises_when_working_directory_is_deleted(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must raise `FileNotFoundError` for a relative path when the working
    directory has been deleted, because the path leads nowhere.
    """
    working_dir = tmp_path / "gone"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    working_dir.rmdir()

    with raises(FileNotFoundError):
        directories("README.template", ["README.template"])


def test_directories_skips_directory_within_templates_directory_that_cannot_be_listed(
    tmp_path: Path,
) -> None:
    """
    `directories` must skip a directory within a templates directory that permissions
    stop it from listing, and that leads to no path to watch, rather than raise, like
    the walk skips it.
    """
    if not can_chmod_deny_listing(tmp_path):
        skip("Permissions don't stop this user from reading directories")

    templates_dir = tmp_path / "templates"
    directory = templates_dir / "locked"
    directory.mkdir(parents=True)

    # Write and search without read, so the directory can't be listed.
    directory.chmod(0o300)

    try:
        found = directories(str(templates_dir), [str(templates_dir)])
    finally:
        directory.chmod(0o755)

    expect = _real(
        tmp_path,
        templates_dir,
    )

    assert found == expect


@mark.parametrize(
    "path",
    [
        # A null character in the file's name only, so `sub`, which exists, would be
        # watched if the path weren't skipped.
        "sub/na\0me.md",
        # A null character in a directory's name, so even the directory that holds
        # the file can't be looked at.
        "s\0ub/name.md",
    ],
)
def test_directories_skips_path_with_null_character(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `directories` must skip a path that holds a null character, like a template that's
    named from values, because nothing can ever be found there, and still watch the
    directories of every other path.
    """
    (tmp_path / "sub").mkdir()
    template = str(tmp_path / "doc.md")

    found = directories(template, [str(tmp_path / path), template])
    expect = _real(tmp_path)

    assert found == expect


def test_directories_stops_at_root_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `directories` must stop climbing at the root directory, even when that can't be
    watched either, like in some containers, so that watching it fails rather than the
    climb never ending.
    """
    monkeypatch.setattr(_module, "_watchable", _never)
    template = str(tmp_path / "doc.md")

    result = directories(template, [template])

    assert result == {"/"}
