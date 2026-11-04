from os import link
from pathlib import Path

from pytest import mark, skip
from rootle.testing import is_treated_as_same_name

from platen.watch import unwritten


@mark.parametrize(
    "path",
    [
        "missing.md",
        # A symlink to a file that doesn't exist.
        "broken.md",
        # A symlink that leads back to itself, so nothing can be found there.
        "loop.md",
        # A null character, which no file can be named with.
        "null\0.md",
    ],
)
def test_unwritten_keeps_template_that_cannot_be_found(
    path: str,
    tmp_path: Path,
) -> None:
    """
    `unwritten` must keep a template that can't be found, rather than raise or match it
    to a destination, so that its creation is seen.
    """
    (tmp_path / "README.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "broken.md").symlink_to("nowhere.md")
    (tmp_path / "loop.md").symlink_to("loop.md")

    kept = unwritten(
        [tmp_path / path],
        [tmp_path / "README.md"],
    )
    expect = [str(tmp_path / path)]

    assert kept == expect


@mark.parametrize(
    ("template", "destination"),
    [
        ("README.md", "README.md"),
        # The destination is a symlink to the template.
        ("README.md", "link.md"),
        # The template is a symlink to the destination.
        ("link.md", "README.md"),
        # The destination is a hard link to the template, so it's the same file by
        # another name, with no symlink to follow.
        ("README.md", "hard.md"),
    ],
)
def test_unwritten_leaves_out_template_that_is_destination(
    template: str,
    destination: str,
    tmp_path: Path,
) -> None:
    """
    `unwritten` must leave out a template that's the same file as a destination,
    however each is named, and keep every other template.
    """
    (tmp_path / "README.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "link.md").symlink_to("README.md")
    link(tmp_path / "README.md", tmp_path / "hard.md")

    kept = unwritten(
        [tmp_path / template, tmp_path / "other.md"],
        [tmp_path / destination],
    )
    expect = [str(tmp_path / "other.md")]

    assert kept == expect


def test_unwritten_leaves_out_template_that_is_destination_in_different_case(
    tmp_path: Path,
) -> None:
    """
    `unwritten` must leave out a template that's the same file as a destination named
    in a different case, on a file system that's insensitive to case, like macOS's
    default APFS.
    """
    if not is_treated_as_same_name(tmp_path, "README.md", "readme.md"):
        skip("'readme.md' names a different file on this file system")

    (tmp_path / "README.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("Hello, world!\n", encoding="utf-8")

    kept = unwritten(
        [tmp_path / "README.md", tmp_path / "other.md"],
        [tmp_path / "readme.md"],
    )
    expect = [str(tmp_path / "other.md")]

    assert kept == expect


@mark.parametrize(
    "destination",
    [
        # The destination is missing, and so is the template at the same path, which
        # must be kept rather than matched to it.
        "README.md",
        "missing/README.md",
        # A symlink that leads back to itself, so nothing can be found there.
        "loop.md",
        # A null character, which no file can be named with.
        "null\0.md",
    ],
)
def test_unwritten_skips_destination_that_cannot_be_found(
    destination: str,
    tmp_path: Path,
) -> None:
    """
    `unwritten` must skip a destination that can't be found, like one that a failed
    press never wrote, rather than raise or match it to a template that can't be found
    either.
    """
    (tmp_path / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "loop.md").symlink_to("loop.md")

    kept = unwritten(
        [tmp_path / "a.md", tmp_path / "README.md"],
        [tmp_path / destination],
    )
    expect = [str(tmp_path / "README.md"), str(tmp_path / "a.md")]

    assert kept == expect


def test_unwritten_sorts_templates(tmp_path: Path) -> None:
    """
    `unwritten` must give the templates that it keeps sorted, whatever order they're
    read in, as paths spelled as they were read.
    """
    (tmp_path / "sub").mkdir()

    for rel_path in ("b.md", "a.md.bak", "sub/a.md", "a.md"):
        (tmp_path / rel_path).write_text("Hello, world!\n", encoding="utf-8")

    kept = unwritten(
        {
            tmp_path / "b.md",
            tmp_path / "sub" / ".." / "a.md.bak",
            tmp_path / "sub" / "a.md",
            tmp_path / "a.md",
        },
        [],
    )
    expect = [
        str(tmp_path / "a.md"),
        str(tmp_path / "b.md"),
        str(tmp_path / "sub" / ".." / "a.md.bak"),
        str(tmp_path / "sub" / "a.md"),
    ]

    assert kept == expect
