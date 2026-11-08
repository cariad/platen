import os
from logging import WARNING
from os import link
from pathlib import Path

from pytest import LogCaptureFixture, MonkeyPatch, mark, raises, skip
from rootle.testing import (
    is_treated_as_same_name,
    snapshot,
)

from platen import (
    DestinationIsTemplateError,
    Platen,
)
from tests.platen.symlinks import symlinked_directories


def test_press_directory_checks_directory_before_working_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must check that it's given a directory before it resolves a
    relative destination, so that a missing working directory doesn't hide the
    directory's error.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    working_dir.rmdir()

    with raises(NotADirectoryError):
        platen.press_directory("doc.md", "out")


def test_press_directory_names_first_of_templates_that_are_the_same_file(
    tmp_path: Path,
) -> None:
    """
    `press_directory` must name the first template that the walk finds when a
    destination is the same file as more than one, like a template and a symlink to it.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)
    (templates_dir / "posts" / "a.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "posts" / "link.md").symlink_to("a.md")

    destination = tmp_path / "out"
    destination.mkdir()
    link(templates_dir / "posts" / "a.md", destination / "a.md")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    with raises(DestinationIsTemplateError) as ex:
        platen.press_directory("posts", destination)

    expect = destination / "a.md"

    assert ex.value.destination_path == expect

    expect = platen.templates_dir / "posts" / "a.md"

    assert ex.value.template_path == expect


def test_press_directory_presses_directory_named_in_different_case(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press a directory named in a different case from how it's
    stored, on a file system that's insensitive to case, like macOS's default APFS, and
    no other directory that starts with the same name.
    """
    if not is_treated_as_same_name(tmp_path, "sub", "SUB"):
        skip("'SUB' names a different directory on this file system")

    templates_dir = tmp_path / "templates"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "subway").mkdir()
    (templates_dir / "sub" / "a.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "subway" / "b.md").write_text("{{ greeting }}\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    result = platen.press_directory("SUB", output_dir)

    assert result == 1

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    expect = [output_dir / "a.md"]

    assert pressed == expect

    result = pressed[0].read_text(encoding="utf-8")

    assert result == "Hello\n"


@mark.parametrize(
    ("directory", "expect"),
    [
        ("linked", ["file.md", "sub/deeper/file.md", "sub/file.md"]),
        ("linked/sub", ["deeper/file.md", "file.md"]),
        # A symlink within the symlink, which is ignored, but is pressed as `sub`, where
        # it leads.
        ("linked/sublinked", ["deeper/file.md", "file.md"]),
        # A directory within a symlink within the symlink.
        ("linked/sublinked/deeper", ["file.md"]),
        # The symlink, named by stepping up out of a real directory beside it.
        ("dir/../linked", ["file.md", "sub/deeper/file.md", "sub/file.md"]),
        # Steps up out of a directory within the symlink, to its target, and back in.
        ("linked/sub/../sub", ["deeper/file.md", "file.md"]),
    ],
)
def test_press_directory_presses_directory_named_through_symlink(
    directory: str,
    expect: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press the directory that a symlink leads to when it's named
    through one, or through a directory within one, even though walks never step into
    symlinks.
    """
    platen = Platen(
        symlinked_directories(tmp_path),
        {},
    )

    result = platen.press_directory(directory, output_dir)
    expect_count = len(expect)

    assert result == expect_count

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == expect


def test_press_directory_presses_directory_named_through_symlink_in_different_case(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press the directory that a symlink leads to when the
    symlink's target is spelled in a different case from how the directory is stored,
    on a file system that's insensitive to case, like macOS's default APFS.
    """
    if not is_treated_as_same_name(tmp_path, "posts", "Posts"):
        skip("'Posts' names a different directory on this file system")

    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)
    (templates_dir / "posts" / "a.md").write_text("Hello\n", encoding="utf-8")
    (templates_dir / "latest").symlink_to("Posts", target_is_directory=True)

    platen = Platen(
        templates_dir,
        {},
    )

    result = platen.press_directory("latest", output_dir)

    assert result == 1

    result = (output_dir / "a.md").read_text(encoding="utf-8")

    assert result == "Hello\n"


def test_press_directory_presses_to_destination(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press every template within a directory to the same relative
    path within the destination, whatever the destination's name, and `.platenignore`
    files above the directory must still apply.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub" / "deep").mkdir(parents=True)
    (templates_dir / ".platenignore").write_text("*.log\n", encoding="utf-8")

    for rel_path in ("file.md", "sub/file.md", "sub/deep/file.md", "sub/notes.log"):
        (templates_dir / rel_path).write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    platen.press_directory("sub", output_dir / "renamed")

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == ["renamed/deep/file.md", "renamed/file.md"]


def test_press_directory_presses_to_sibling_within_templates_dir(
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press a directory to a destination within the templates
    directory, so long as the destination isn't within the directory being pressed.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "docs-src").mkdir(parents=True)
    (templates_dir / "docs-src" / "file.md").write_text(
        "{{ greeting }}\n",
        encoding="utf-8",
    )

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_directory("docs-src", templates_dir / "docs")

    pressed = templates_dir / "docs" / "file.md"
    result = pressed.read_text(encoding="utf-8")

    assert result == "Hello\n"


def test_press_directory_raises_when_destination_is_earlier_template(
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise `DestinationIsTemplateError` when a destination is the
    same file as a template that the press has already pressed, rather than overwriting
    the template.

    The press stops there, so the templates pressed before it stay pressed.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)

    for rel_path in ("posts/a.md", "posts/b.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    destination = tmp_path / "out"
    destination.mkdir()
    link(templates_dir / "posts" / "a.md", destination / "b.md")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    with raises(DestinationIsTemplateError) as ex:
        platen.press_directory("posts", destination)

    expect = destination / "b.md"

    assert ex.value.destination_path == expect

    expect = platen.templates_dir / "posts" / "a.md"

    assert ex.value.template_path == expect

    result = (destination / "a.md").read_text(encoding="utf-8")

    assert result == "Hello\n"

    template = templates_dir / "posts" / "a.md"
    result = template.read_text(encoding="utf-8")

    assert result == "{{ greeting }}\n"


def test_press_directory_raises_when_destination_is_template_outside_directory(
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise `DestinationIsTemplateError` when a destination is the
    same file as a template outside the directory being pressed, so that pressing into
    the templates directory can't overwrite a template that a later one includes.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "pages").mkdir(parents=True)
    (templates_dir / "shared").mkdir()
    (templates_dir / "pages" / "footer.md").write_text("Pressed\n", encoding="utf-8")

    (templates_dir / "pages" / "z.md").write_text(
        '{% include "shared/footer.md" %}',
        encoding="utf-8",
    )

    (templates_dir / "shared" / "footer.md").write_text("Original\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    before = snapshot(tmp_path)

    with raises(DestinationIsTemplateError) as ex:
        platen.press_directory("pages", templates_dir / "shared")

    expect = templates_dir / "shared" / "footer.md"

    assert ex.value.destination_path == expect

    expect = platen.templates_dir / "shared" / "footer.md"

    assert ex.value.template_path == expect

    result = snapshot(tmp_path)

    assert result == before


def test_press_directory_raises_when_destination_is_template_via_missing_dir(
    tmp_path: Path,
) -> None:
    """
    `press_directory` must identify a walked template that's a symlink where `realpath`
    leads, even when its target steps up out of a directory that doesn't exist, so that
    a destination that's the same file is refused rather than failing to render.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "doc.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "sub" / "link").symlink_to("missing/../../doc.md")

    # Keep the target out of the walk, so that only the symlink can protect it.
    (templates_dir / ".platenignore").write_text("/doc.md\n", encoding="utf-8")

    destination = tmp_path / "out"
    destination.mkdir()
    link(templates_dir / "doc.md", destination / "link")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    before = snapshot(tmp_path)

    with raises(DestinationIsTemplateError) as ex:
        platen.press_directory("sub", destination)

    expect = destination / "link"

    assert ex.value.destination_path == expect

    expect = platen.templates_dir / "sub" / "link"

    assert ex.value.template_path == expect

    result = snapshot(tmp_path)

    assert result == before


@mark.parametrize(
    ("directory", "expect", "expect_path"),
    [
        ("doc.md", NotADirectoryError, "doc.md"),
        # A FIFO, which isn't a directory either.
        ("pipe", NotADirectoryError, "pipe"),
        # A broken symlink, so the error names `nowhere`, where it leads.
        ("broken", FileNotFoundError, "nowhere"),
        ("missing", FileNotFoundError, "missing"),
    ],
)
def test_press_directory_raises_when_directory_is_not_directory(
    directory: str,
    expect: type[OSError],
    expect_path: str,
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise when it's asked to press anything but a directory,
    rather than pressing something else or warning that there's nothing to press.

    A directory named through a symlink is named by where the symlink leads, even when
    nothing's there.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "broken").symlink_to(templates_dir / "nowhere")

    if directory == "pipe":
        if not hasattr(os, "mkfifo"):
            skip("This platform doesn't have FIFOs")

        os.mkfifo(templates_dir / "pipe")

    platen = Platen(
        templates_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"), raises(expect) as ex:
        platen.press_directory(directory, output_dir)

    expect_filename = str(platen.templates_dir / expect_path)

    assert ex.value.filename == expect_filename
    assert not caplog.records
    assert not output_dir.exists()


@mark.parametrize(
    "directory",
    [
        # Holds only a draft, which `.platenignore` ignores.
        "drafts",
        "empty_dir",
        "ignored_dir",
    ],
)
def test_press_directory_warns_when_directory_has_nothing_to_press(
    directory: str,
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must log a warning and return 0 when a directory holds nothing to
    press, even when other directories do.

    A directory holds nothing to press when it's empty, when it's ignored, or when
    everything in it is ignored.
    """
    templates_dir = tmp_path / "source"

    for rel_dir in ("drafts", "empty_dir", "ignored_dir"):
        (templates_dir / rel_dir).mkdir(parents=True)

    (templates_dir / ".platenignore").write_text(
        "*.draft.md\nignored_dir/\n",
        encoding="utf-8",
    )

    (templates_dir / "drafts" / "file.draft.md").write_text("", encoding="utf-8")
    (templates_dir / "ignored_dir" / "file.md").write_text("", encoding="utf-8")
    (templates_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"):
        pressed = platen.press_directory(directory, output_dir / directory)

    assert pressed == 0

    expect = (
        f"Nothing to press in {platen.templates_dir / directory}: it's "
        "empty, or everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
    assert not output_dir.exists()
