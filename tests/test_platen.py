from errno import EEXIST, EISDIR, ELOOP, ENOENT, ENOTDIR
from logging import WARNING
from os import getcwd, link, strerror
from os.path import exists, join, realpath
from pathlib import Path
from re import escape
from stat import S_IMODE

from jinja2 import UndefinedError
from pytest import LogCaptureFixture, MonkeyPatch, mark, raises, skip

from platen import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    Platen,
    TemplateNotInDirectoryError,
)
from platen.files import identity
from tests.snapshots import snapshot


def _symlinked_directories(tmp_path: Path) -> Path:
    """
    Create a templates directory that holds `linked`, a symlink to a directory that
    holds another symlink to a directory, and return the templates directory's path.
    """
    templates_dir = tmp_path / "source"
    real_dir = templates_dir / "real"
    (real_dir / "sub" / "deeper").mkdir(parents=True)
    (templates_dir / "dir").mkdir()
    (real_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "sub" / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "sublinked").symlink_to(real_dir / "sub", target_is_directory=True)
    (templates_dir / "linked").symlink_to(real_dir, target_is_directory=True)
    return templates_dir


def test_press_checks_template_before_working_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must check what kind of template they're given
    before they make a relative destination absolute, so that a missing working
    directory doesn't hide the template's error.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    working_dir.rmdir()

    with raises(IsADirectoryError):
        platen.press_file("sub", "out.md")

    with raises(NotADirectoryError):
        platen.press_directory("doc.md", "out")


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
    assert pressed.read_text(encoding="utf-8") == "Hello\n"


@mark.parametrize(
    "directory",
    [
        "linked",
        "linked/sub",
        "linked/sublinked",
        "linked/sublinked/deeper",
        "dir/../linked",
        "linked/sub/../sub",
    ],
    ids=[
        "symlink",
        "directory within",
        "symlink within",
        "directory within symlink within",
        "symlink via parent",
        "directory within via parent",
    ],
)
def test_press_directory_raises_for_symlink_to_directory(
    directory: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise `NotADirectoryError` when it's asked to press a symlink
    to a directory, or a directory within one.

    Symlinks are pressed like files and never walked into, so a walk would find nothing
    to press. The error must name the outermost symlink, and nothing must be pressed.
    """
    platen = Platen(
        _symlinked_directories(tmp_path),
        {},
    )

    with raises(NotADirectoryError) as ex:
        platen.press_directory(directory, output_dir)

    assert ex.value.errno == ENOTDIR
    assert ex.value.filename == str(platen.templates_dir / "linked")
    assert (
        ex.value.strerror == f"{strerror(ENOTDIR)} (Platen never walks into symlinks)"
    )
    assert ex.value.filename2 is None
    assert not output_dir.exists()


@mark.parametrize(
    ("destination", "expect", "expect_destination"),
    [
        ("dir_in_way", IsADirectoryError, "dir_in_way/z.md"),
        (
            "missing/../dir_in_way",
            IsADirectoryError,
            "missing/../dir_in_way/z.md",
        ),
        ("file_in_way", NotADirectoryError, "file_in_way/sub/b.md"),
        (
            "missing/../file_in_way",
            NotADirectoryError,
            "missing/../file_in_way/sub/b.md",
        ),
        (
            "file_in_way/sub/../out",
            NotADirectoryError,
            "file_in_way/sub/../out",
        ),
        ("dangling", FileNotFoundError, "dangling/z.md"),
        ("beneath_file", NotADirectoryError, "beneath_file/z.md"),
        ("into_result", NotADirectoryError, "into_result/sub/b.md"),
        ("case_variant", NotADirectoryError, "case_variant/sub/b.md"),
    ],
    ids=[
        "directory in the way of a file",
        "directory in the way of a file, via parent of missing directory",
        "file in the way of a directory",
        "file in the way of a directory, via parent of missing directory",
        "via parent of file",
        "symlink into missing directory",
        "symlink beneath file",
        "symlink into another result",
        "symlink into existing result, by a different case",
    ],
)
def test_press_directory_raises_when_destination_is_in_the_way(
    destination: str,
    expect: type[OSError],
    expect_destination: str,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise before writing anything when something within the
    destination is in the way of a template's result, wherever symlinks lead, rather
    than failing partway through writing.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)

    for rel_path in ("a.md", "sub/b.md", "z.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    for name in (
        "dir_in_way",
        "file_in_way",
        "dangling",
        "beneath_file",
        "into_result",
        "case_variant",
    ):
        (tmp_path / name).mkdir()

    (tmp_path / "dir_in_way" / "z.md").mkdir()
    (tmp_path / "file_in_way" / "sub").write_text("In the way\n", encoding="utf-8")
    (tmp_path / "in_the_way").write_text("In the way\n", encoding="utf-8")
    (tmp_path / "dangling" / "z.md").symlink_to(tmp_path / "nowhere" / "z.md")
    (tmp_path / "beneath_file" / "z.md").symlink_to(tmp_path / "in_the_way" / "z.md")
    (tmp_path / "into_result" / "sub").symlink_to(tmp_path / "into_result" / "a.md")
    (tmp_path / "case_variant" / "a.md").write_text("Old\n", encoding="utf-8")
    (tmp_path / "case_variant" / "sub").symlink_to(tmp_path / "case_variant" / "A.md")

    # A different case only names the same file on case-insensitive file systems, like
    # macOS's default APFS.
    if destination == "case_variant" and not (tmp_path / destination / "A.md").exists():
        skip("'A.md' names a different file on this file system")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    before = snapshot(tmp_path)

    with raises(expect) as ex:
        platen.press_directory(".", tmp_path / destination)

    assert ex.value.filename == str(tmp_path / expect_destination)
    assert snapshot(tmp_path) == before


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
    (templates_dir / "sub" / "a.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "sub" / "link").symlink_to("missing/../../doc.md")

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

    assert ex.value.destination_path == destination / "link"
    assert ex.value.template_path == platen.templates_dir / "sub" / "link"
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("alias", "expect_destination", "expect_claimed"),
    [
        ("symlink", "sub/a.md", "a.md"),
        ("hard link", "b.md", "a.md"),
    ],
    ids=[
        "symlink to destination",
        "hard link between destinations",
    ],
)
def test_press_directory_raises_when_destinations_collide(
    alias: str,
    expect_destination: str,
    expect_claimed: str,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise `FileExistsError` before writing anything when two
    templates would be pressed to the same file, rather than letting one result
    overwrite the other.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)

    for rel_path in ("a.md", "b.md", "sub/a.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    destination = tmp_path / "build"
    destination.mkdir()

    if alias == "symlink":
        (destination / "sub").symlink_to(destination, target_is_directory=True)
    else:
        (destination / "a.md").write_text("Old\n", encoding="utf-8")
        link(destination / "a.md", destination / "b.md")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    before = snapshot(tmp_path)

    with raises(FileExistsError) as ex:
        platen.press_directory(".", destination)

    assert ex.value.errno == EEXIST
    assert ex.value.filename == str(destination / expect_destination)
    assert ex.value.filename2 == str(destination / expect_claimed)
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("directory", "expect"),
    [
        ("doc.md", NotADirectoryError),
        ("doc.md/..", NotADirectoryError),
        ("broken", NotADirectoryError),
        ("missing", FileNotFoundError),
    ],
    ids=[
        "file",
        "parent of file",
        "broken symlink",
        "missing",
    ],
)
def test_press_directory_raises_when_directory_is_not_directory(
    directory: str,
    expect: type[OSError],
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise when it's asked to press anything but a directory,
    rather than pressing something else or warning that there's nothing to press.

    The directory must be judged the way the operating system sees it, so a path like
    "doc.md/.." must be refused rather than tidied up into the templates directory.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "broken").symlink_to(templates_dir / "nowhere")

    platen = Platen(
        templates_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"), raises(expect) as ex:
        platen.press_directory(directory, output_dir)

    assert ex.value.filename == join(platen.templates_dir, directory)
    assert not caplog.records
    assert not output_dir.exists()


def test_press_directory_raises_when_directory_vanishes(
    monkeypatch: MonkeyPatch,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must raise `FileNotFoundError` when the directory being pressed
    is gone by the time its destinations are checked, rather than letting the check
    that nothing's written within it pass.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "sub" / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    vanished = platen.templates_dir / "sub"

    # Simulate the directory being moved between the walk and the checks.
    def identify(path: Path) -> tuple[int, int] | None:
        return None if path == vanished else identity(path)

    monkeypatch.setattr("platen.platen.identity", identify)

    with raises(FileNotFoundError) as ex:
        platen.press_directory("sub", output_dir)

    assert ex.value.errno == ENOENT
    assert ex.value.filename == str(vanished)
    assert not output_dir.exists()


@mark.parametrize(
    "directory",
    [
        "drafts",
        "empty_dir",
        "ignored_dir",
    ],
    ids=[
        "everything in directory ignored",
        "empty directory",
        "ignored directory",
    ],
)
def test_press_directory_warns_when_directory_has_nothing_to_press(
    directory: str,
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must log a warning when a directory holds nothing to press, even
    when other directories do.

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
        platen.press_directory(directory, output_dir / directory)

    expect = (
        f"Nothing to press in {platen.templates_dir / directory}: it's "
        "empty, or everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
    assert not output_dir.exists()


def test_press_file_follows_symlink_before_parent(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must step up from a symlink with `..` the same way the operating system
    does: from the symlink's target, not from the directory that holds the symlink.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "real" / "sub").mkdir(parents=True)
    (templates_dir / "doc.md").write_text("Wrong\n", encoding="utf-8")
    (templates_dir / "real" / "doc.md").write_text("Right\n", encoding="utf-8")
    (templates_dir / "linked").symlink_to(
        templates_dir / "real" / "sub",
        target_is_directory=True,
    )

    platen = Platen(
        templates_dir,
        {},
    )

    platen.press_file("linked/../doc.md", output_dir / "doc.md")

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "doc.md"]
    assert pressed[0].read_text(encoding="utf-8") == "Right\n"


@mark.parametrize(
    ("template", "expect"),
    [
        (".platenignore", "ignored.md\nignored_dir/\n"),
        ("ignored.md", "Hello, world!\n"),
        ("ignored_dir/file.md", "Hello, world!\n"),
    ],
    ids=[
        "ignore file",
        "ignored file",
        "file within ignored directory",
    ],
)
def test_press_file_presses_template_even_if_ignored(
    template: str,
    expect: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must press the template it's given, even when a `.platenignore` file
    would ignore it.

    `.platenignore` files only apply when walking a directory, and never to an
    explicitly named template.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "ignored_dir").mkdir(parents=True)

    (templates_dir / ".platenignore").write_text(
        "ignored.md\nignored_dir/\n",
        encoding="utf-8",
    )

    for rel_path in ("ignored.md", "ignored_dir/file.md"):
        (templates_dir / rel_path).write_text(
            "{{ greeting }}, world!\n",
            encoding="utf-8",
        )

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_file(template, output_dir / template)

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / template]
    assert pressed[0].read_text(encoding="utf-8") == expect


@mark.parametrize(
    "template",
    [
        "linked/file.md",
        "linked/sub/../file.md",
    ],
    ids=[
        "named",
        "named via parent",
    ],
)
def test_press_file_presses_template_within_symlink_to_directory(
    template: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must press a template within a symlink to a directory with its
    target's content.
    """
    templates_dir = tmp_path / "source"
    real_dir = templates_dir / "real"
    (real_dir / "sub").mkdir(parents=True)
    (real_dir / "file.md").write_text("{{ greeting }}, world!\n", encoding="utf-8")
    (templates_dir / "linked").symlink_to(real_dir, target_is_directory=True)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_file(template, output_dir / "file.md")

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "file.md"]
    assert pressed[0].read_text(encoding="utf-8") == "Hello, world!\n"


def test_press_file_presses_to_absolute_destination(
    tmp_path: Path,
) -> None:
    """`press_file` must press a template to an absolute destination."""
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "file.md").write_text("{{ greeting }}\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    destination = tmp_path / "elsewhere" / "file.md"
    platen.press_file("file.md", destination)

    assert destination.read_text(encoding="utf-8") == "Hello\n"


@mark.parametrize(
    ("template", "destination", "expect"),
    [
        ("file.md", "source/renamed.md", "source/renamed.md"),
        ("file.md", "build/new/deeper/file.md", "build/new/deeper/file.md"),
        ("file.md", "build/../elsewhere/file.md", "elsewhere/file.md"),
        ("blob.bin", "build/renamed.bin", "build/renamed.bin"),
    ],
    ids=[
        "renamed within templates directory",
        "within new directories",
        "via parent",
        "binary",
    ],
)
def test_press_file_presses_to_destination(
    template: str,
    destination: str,
    expect: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` must press a template to exactly the destination it's given, relative
    to the working directory, creating any missing directories.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "file.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "blob.bin").write_bytes(b"\x00\xff\xfe binary\n")

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_file(template, destination)

    pressed = tmp_path / expect
    body = b"Hello\n" if template == "file.md" else b"\x00\xff\xfe binary\n"
    assert pressed.read_bytes() == body


@mark.parametrize(
    "body",
    [
        b"{{ greeting }}, world!\n",
        b"\x00\xff\xfe binary\n",
    ],
    ids=[
        "text",
        "binary",
    ],
)
def test_press_file_presses_to_device_without_changing_its_permissions(
    body: bytes,
    tmp_path: Path,
) -> None:
    """
    `press_file` must press a template to a device like `/dev/null` without giving the
    device its template's permissions.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    template = templates_dir / "file"
    template.write_bytes(body)
    template.chmod(0o700)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    device = Path("/dev/null")
    before = S_IMODE(device.stat().st_mode)

    platen.press_file("file", device)

    assert S_IMODE(device.stat().st_mode) == before


def test_press_file_presses_to_same_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` must press a template to a destination in the same directory, like
    `./README.template` to `./README.md`.

    Referenced templates must resolve relative to the templates directory, the template
    must be left as it was, and pressing again must overwrite the previous result.
    """
    monkeypatch.chdir(tmp_path)

    template = Path("README.template")
    template.write_text(
        '# {{ name }}\n\n{% include "_badge.txt" %}\n',
        encoding="utf-8",
    )
    template.chmod(0o640)
    Path("_badge.txt").write_text("Build: {{ status }}\n", encoding="utf-8")
    template_body = template.read_bytes()

    for status in ("failing", "passing"):
        platen = Platen(
            ".",
            {"name": "platen", "status": status},
        )

        platen.press_file("README.template", "README.md")

        pressed = tmp_path / "README.md"
        expect = f"# platen\n\nBuild: {status}\n"
        assert pressed.read_text(encoding="utf-8") == expect
        assert S_IMODE(pressed.stat().st_mode) == 0o640
        assert template.read_bytes() == template_body


def test_press_file_raises_for_missing_template(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `FileNotFoundError` when its template doesn't exist, rather
    than pressing nothing.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(FileNotFoundError) as ex:
        platen.press_file("missing.md", output_dir / "missing.md")

    assert ex.value.filename == str(platen.templates_dir / "missing.md")


@mark.parametrize(
    "spelling",
    [
        "build",
        "missing/../build",
    ],
    ids=[
        "as named",
        "via parent of missing directory",
    ],
)
@mark.parametrize(
    "body",
    [
        b"{{ missing }}\n",
        b"\x00\xff\xfe binary\n",
    ],
    ids=[
        "text",
        "binary",
    ],
)
def test_press_file_raises_when_destination_is_directory(
    body: bytes,
    spelling: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `IsADirectoryError` before rendering when it's asked to
    press a template to a directory, by any spelling, rather than pressing into it.

    Rendering the text template would raise `UndefinedError` instead. The error must
    name the destination absolutely, but as it was given.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "file").write_bytes(body)

    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {},
    )

    before = snapshot(tmp_path)

    with raises(IsADirectoryError) as ex:
        platen.press_file("file", spelling)

    assert ex.value.errno == EISDIR
    assert ex.value.filename == str(tmp_path / spelling)
    assert snapshot(tmp_path) == before


@mark.parametrize(
    "rel_path",
    [
        "doc.md",
        "new/doc.md",
        "../doc.md",
    ],
    ids=[
        "within file",
        "within missing directory within file",
        "via parent of file",
    ],
)
def test_press_file_raises_when_destination_is_within_file(
    rel_path: str,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `NotADirectoryError` before rendering when its destination
    is within a file, however deeply, or steps up out of one with "..", like the
    operating system does.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    # Rendering this template would raise `UndefinedError` instead.
    (templates_dir / "doc.md").write_text("{{ missing }}\n", encoding="utf-8")

    in_the_way = tmp_path / "build"
    in_the_way.write_text("In the way\n", encoding="utf-8")
    destination = in_the_way / rel_path

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(NotADirectoryError) as ex:
        platen.press_file("doc.md", destination)

    assert ex.value.errno == ENOTDIR
    assert ex.value.filename == str(destination)
    assert in_the_way.read_text(encoding="utf-8") == "In the way\n"
    assert not (tmp_path / "doc.md").exists()


@mark.parametrize(
    "destination",
    [
        "build/",
        "build/.",
        "build/x/..",
    ],
    ids=[
        "trailing separator",
        "trailing dot",
        "trailing parent",
    ],
)
def test_press_file_raises_when_destination_names_directory(
    destination: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `IsADirectoryError` before rendering when its destination
    names a directory, like "build/", even when there's no directory there yet, and
    mustn't create one.

    `pathlib` drops a trailing separator, so the destination mustn't be pressed to a
    file named "build" instead.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    # Rendering this template would raise `UndefinedError` instead.
    (templates_dir / "doc.md").write_text("{{ missing }}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(IsADirectoryError) as ex:
        platen.press_file("doc.md", destination)

    assert ex.value.errno == EISDIR
    assert ex.value.filename == join(getcwd(), destination)
    assert not (tmp_path / "build").exists()


@mark.parametrize(
    "template",
    [
        ".",
        "dir",
        "linked",
        "linked/sub",
    ],
    ids=[
        "templates directory",
        "directory",
        "symlink to directory",
        "directory within symlink",
    ],
)
def test_press_file_raises_when_template_is_directory(
    template: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `IsADirectoryError` when it's asked to press a directory,
    or a symlink to one, rather than pressing what's within it.
    """
    platen = Platen(
        _symlinked_directories(tmp_path),
        {},
    )

    with raises(IsADirectoryError) as ex:
        platen.press_file(template, output_dir / "file.md")

    assert ex.value.errno == EISDIR
    assert ex.value.filename == str(platen.templates_dir / template)
    assert not output_dir.exists()


@mark.parametrize(
    ("template", "expect"),
    [
        ("doc.md/", NotADirectoryError),
        ("missing/../doc.md", FileNotFoundError),
    ],
    ids=[
        "trailing separator",
        "via parent of missing directory",
    ],
)
def test_press_file_raises_when_template_is_not_as_named(
    template: str,
    expect: type[OSError],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must judge its template the way the operating system sees it, so a
    path that the operating system can't open must be refused rather than tidied up.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(expect) as ex:
        platen.press_file(template, output_dir / "doc.md")

    assert ex.value.filename == join(platen.templates_dir, template)
    assert not output_dir.exists()


@mark.parametrize(
    "body",
    [
        b"echo hello\n",
        b"\x00\xff\xfe binary\n",
    ],
    ids=[
        "text",
        "binary",
    ],
)
def test_press_file_updates_permissions(
    body: bytes,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must update the pressed file's permissions.

    Pressed files must have exactly the same permissions as their
    templates. Overwriting an existing file doesn't change its
    permissions, so this test specifically tests that re-pressing a
    template after its permissions changed updates the pressed file.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    template = templates_dir / "script.sh"
    template.write_bytes(body)

    platen = Platen(
        templates_dir,
        {},
    )

    pressed = output_dir / "script.sh"

    for mode in (0o755, 0o644, 0o700):
        template.chmod(mode)
        platen.press_file("script.sh", pressed)
        assert S_IMODE(pressed.stat().st_mode) == mode


def test_press_ignores_protected_file_that_does_not_exist(tmp_path: Path) -> None:
    """A protected file that doesn't exist must not stop a press."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
        protect=[tmp_path / "missing.yaml"],
    )

    platen.press_file("doc.md", tmp_path / "doc.md")
    assert (tmp_path / "doc.md").read_text(encoding="utf-8") == "Hello, world!\n"


def test_press_presses_into_directory_that_contains_templates_dir(
    tmp_path: Path,
) -> None:
    """
    `press` must press into a directory that contains the templates directory, so long
    as no destination is within the templates directory.
    """
    project_dir = tmp_path / "project"
    templates_dir = project_dir / "templates"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "file.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "sub" / "file.md").write_text("{{ greeting }}\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press(project_dir)

    assert (project_dir / "file.md").read_text(encoding="utf-8") == "Hello\n"
    assert (project_dir / "sub" / "file.md").read_text(encoding="utf-8") == "Hello\n"
    assert (templates_dir / "file.md").read_text(encoding="utf-8") == "{{ greeting }}\n"


@mark.parametrize(
    ("method", "templates", "destinations"),
    [
        ("press_directory", ["links"], ["links"]),
        (
            "press_file",
            ["links/blob.bin", "links/file.md"],
            ["links/blob.bin", "links/file.md"],
        ),
        (
            "press_file",
            ["links/../links/blob.bin", "links/../links/file.md"],
            ["links/blob.bin", "links/file.md"],
        ),
    ],
    ids=["walked", "named", "named via parent"],
)
def test_press_presses_symlink_to_file_with_target_content(
    method: str,
    templates: list[str],
    destinations: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must press a symlink to a file with its target's
    content, whether it's named or found by walking a directory.

    The pressed file must be a regular file, not a copy of the symlink, and a walk must
    press it to the symlink's own path, not the target's.
    """
    templates_dir = tmp_path / "source"
    links_dir = templates_dir / "links"
    links_dir.mkdir(parents=True)

    blob = b"\x00\xff\xfe binary\n"
    (templates_dir / "blob.bin").write_bytes(blob)
    (templates_dir / "file.md").write_text(
        "{{ greeting }}, world!\n",
        encoding="utf-8",
    )

    for name in ("blob.bin", "file.md"):
        (links_dir / name).symlink_to(Path("..") / name)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory

    for template, destination in zip(templates, destinations, strict=True):
        press(template, output_dir / destination)

    pressed_blob = output_dir / "links" / "blob.bin"
    pressed_file = output_dir / "links" / "file.md"

    assert not pressed_blob.is_symlink()
    assert not pressed_file.is_symlink()
    assert pressed_blob.read_bytes() == blob
    assert pressed_file.read_text(encoding="utf-8") == "Hello, world!\n"
    assert not (output_dir / "blob.bin").exists()
    assert not (output_dir / "file.md").exists()


@mark.parametrize(
    ("method", "template", "destination", "expect"),
    [
        ("press_directory", "alias", ".", ["file.md", "sub/file.md"]),
        ("press_directory", "alias/sub", "sub", ["sub/file.md"]),
        ("press_file", "alias/sub/file.md", "sub/file.md", ["sub/file.md"]),
        ("press_file", "alias/linked/file.md", "linked/file.md", ["linked/file.md"]),
        ("press_file", "../alias/sub/file.md", "sub/file.md", ["sub/file.md"]),
    ],
    ids=[
        "directory",
        "subdirectory",
        "file",
        "file within symlink",
        "relative",
    ],
)
def test_press_presses_through_symlink_to_templates_dir(
    method: str,
    template: str,
    destination: str,
    expect: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must press templates named through a symlink to
    the templates directory, like the symlink that the templates directory was given as.

    On macOS, for example, temporary directories are given through `/var` but resolve
    to `/private/var`.
    """
    real_dir = tmp_path / "real"
    (real_dir / "sub").mkdir(parents=True)
    (real_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "sub" / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "linked").symlink_to(real_dir / "sub", target_is_directory=True)

    # Walks can't press symlinks to directories, so keep this one out of them.
    (real_dir / ".platenignore").write_text("linked\n", encoding="utf-8")

    alias = tmp_path / "alias"
    alias.symlink_to(real_dir, target_is_directory=True)

    platen = Platen(
        alias,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory

    named = Path(template)
    named = named if named.parts[0] == ".." else tmp_path / named
    press(named, output_dir / destination)

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == expect


@mark.parametrize(
    ("method", "template", "destination"),
    [
        ("press", "", ""),
        ("press_directory", "", "build"),
        ("press_directory", "sub", ""),
        ("press_file", "", "build.md"),
        ("press_file", "doc.md", ""),
        ("press_file", "../outside.md", ""),
    ],
    ids=[
        "press, empty destination",
        "press_directory, empty directory",
        "press_directory, empty destination",
        "press_file, empty template",
        "press_file, empty destination",
        "press_file, empty destination before template outside",
    ],
)
def test_press_raises_for_empty_path(
    method: str,
    template: str,
    destination: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Every press method must raise `FileNotFoundError` for an empty string, like the
    operating system does, rather than reading it as ".".

    An empty template mustn't mean the whole templates directory, and an empty
    destination mustn't mean the working directory.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "sub" / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {},
    )

    before = snapshot(tmp_path)

    with raises(FileNotFoundError) as ex:
        if method == "press":
            platen.press(destination)
        elif method == "press_directory":
            platen.press_directory(template, destination)
        else:
            platen.press_file(template, destination)

    assert ex.value.errno == ENOENT
    assert ex.value.strerror == strerror(ENOENT)
    assert ex.value.filename == ""
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("method", "expect", "expect_errno"),
    [
        ("press_file", OSError, ELOOP),
        ("press_directory", NotADirectoryError, ENOTDIR),
    ],
    ids=[
        "press_file",
        "press_directory",
    ],
)
def test_press_raises_for_symlink_loop(
    method: str,
    expect: type[OSError],
    expect_errno: int,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must raise the same `OSError` for a loop of
    symlinks on every version of Python, rather than `RuntimeError` on some.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "loop").symlink_to("loop")

    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory

    with raises(OSError) as ex:
        press("loop", output_dir)

    assert type(ex.value) is expect
    assert ex.value.errno == expect_errno
    assert not output_dir.exists()


@mark.parametrize(
    ("method", "template", "destination"),
    [
        ("press_file", "doc.md", "loop/doc.md"),
        ("press_directory", ".", "loop"),
    ],
    ids=[
        "press_file",
        "press_directory",
    ],
)
def test_press_raises_for_symlink_loop_in_destination(
    method: str,
    template: str,
    destination: str,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must raise the same `OSError` for a loop of
    symlinks in the destination on every version of Python, rather than `RuntimeError`
    on some, and nothing must be written.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "loop").symlink_to("loop")

    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    before = snapshot(tmp_path)

    with raises(OSError) as ex:
        press(template, tmp_path / destination)

    assert type(ex.value) is OSError
    assert ex.value.errno == ELOOP
    assert snapshot(tmp_path) == before


def test_press_raises_for_symlink_to_directory_within(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must raise `IsADirectoryError` when the templates directory holds a symlink
    to a directory, and nothing must be pressed.

    Symlinks are pressed like files and never walked into, so a symlink to a directory
    can't be pressed. The error must name the outermost symlink.
    """
    platen = Platen(
        _symlinked_directories(tmp_path),
        {},
    )

    with raises(IsADirectoryError) as ex:
        platen.press(output_dir)

    assert ex.value.filename == str(platen.templates_dir / "linked")
    assert not output_dir.exists()


@mark.parametrize(
    "spelling",
    [
        "build.md",
        "missing/../build.md",
    ],
    ids=[
        "as named",
        "via parent of missing directory",
    ],
)
@mark.parametrize(
    "directory",
    [
        None,
        "ignored_dir",
    ],
    ids=[
        "templates directory",
        "ignored directory",
    ],
)
def test_press_raises_when_destination_is_file(
    directory: str | None,
    spelling: str,
    tmp_path: Path,
) -> None:
    """
    `press` and `press_directory` must raise `NotADirectoryError` when they're asked to
    press into an existing file, by any spelling, even when the directory holds nothing
    to press.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "ignored_dir").mkdir(parents=True)
    (templates_dir / ".platenignore").write_text("ignored_dir/\n", encoding="utf-8")
    (templates_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "ignored_dir" / "file.md").write_text("", encoding="utf-8")

    (tmp_path / "build.md").write_text("Not a directory\n", encoding="utf-8")
    destination = tmp_path / spelling

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(NotADirectoryError) as ex:
        if directory is None:
            platen.press(destination)
        else:
            platen.press_directory(directory, destination)

    assert ex.value.errno == ENOTDIR
    assert ex.value.filename == str(destination)
    assert (tmp_path / "build.md").read_text(encoding="utf-8") == "Not a directory\n"


@mark.parametrize(
    ("method", "destination", "expect_destination"),
    [
        ("press_file", "values.yaml", "values.yaml"),
        ("press_file", "link.yaml", "link.yaml"),
        ("press_file", "hard.yaml", "hard.yaml"),
        ("press_file", "missing/../values.yaml", "missing/../values.yaml"),
        ("press_directory", ".", "doc.md"),
    ],
    ids=[
        "file to same path",
        "file to symlink",
        "file to hard link",
        "file through missing directory",
        "directory",
    ],
)
def test_press_raises_when_destination_is_protected(
    method: str,
    destination: str,
    expect_destination: str,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must raise `DestinationIsProtectedError` when a
    destination is the same file as a protected file, by any name, and nothing must
    change.
    """
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (tmp_path / "values.yaml").write_text("greeting: Hello\n", encoding="utf-8")
    (tmp_path / "link.yaml").symlink_to("values.yaml")
    link(tmp_path / "values.yaml", tmp_path / "hard.yaml")
    link(tmp_path / "values.yaml", tmp_path / "doc.md")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
        protect=[tmp_path / "values.yaml"],
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    template = "doc.md" if method == "press_file" else "."
    before = snapshot(tmp_path)

    expect = (
        f"Destination '{tmp_path / expect_destination}' is the same file as "
        f"protected file '{tmp_path / 'values.yaml'}'"
    )

    with raises(DestinationIsProtectedError, match=escape(expect)) as ex:
        press(template, tmp_path / destination)

    assert ex.value.destination_path == tmp_path / expect_destination
    assert ex.value.protected_path == tmp_path / "values.yaml"
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("method", "template", "destination", "expect_template", "expect_destination"),
    [
        ("press_file", "doc.md", "templates/doc.md", "doc.md", "templates/doc.md"),
        (
            "press_file",
            "doc.md",
            "templates/posts/../doc.md",
            "doc.md",
            "templates/posts/../doc.md",
        ),
        (
            "press_file",
            "doc.md",
            "missing/../templates/doc.md",
            "doc.md",
            "missing/../templates/doc.md",
        ),
        (
            "press_file",
            "blob.bin",
            "missing/../templates/blob.bin",
            "blob.bin",
            "missing/../templates/blob.bin",
        ),
        ("press_file", "doc.md", "doc_link.md", "doc.md", "doc_link.md"),
        ("press_file", "doc.md", "doc_hard.md", "doc.md", "doc_hard.md"),
        ("press_directory", "posts", "out", "posts/link.md", "out/a.md"),
        ("press_directory", "posts", "out_later", "posts/a.md", "out_later/link.md"),
        ("press_file", "doc.md", "templates/DOC.md", "doc.md", "templates/DOC.md"),
        (
            "press_file",
            "caf\u00e9.md",
            "templates/cafe\u0301.md",
            "caf\u00e9.md",
            "templates/cafe\u0301.md",
        ),
    ],
    ids=[
        "same path",
        "via parent",
        "via parent of missing directory",
        "binary via parent of missing directory",
        "symlink to template",
        "hard link to template",
        "hard link to another template",
        "hard link to earlier template",
        "different case",
        "different normalisation",
    ],
)
def test_press_raises_when_destination_is_template(
    method: str,
    template: str,
    destination: str,
    expect_template: str,
    expect_destination: str,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must raise `DestinationIsTemplateError` when a
    destination is the same file as a template being pressed, by any name, and nothing
    must change.

    Every destination must be checked before anything is written, so a destination
    that's a template must be refused even when it comes after others.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)
    (templates_dir / "shared").mkdir()

    # Each template renders to something other than itself, so that an overwrite would
    # change it.
    for rel_path in ("doc.md", "caf\u00e9.md", "posts/a.md", "shared/b.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    (templates_dir / "blob.bin").write_bytes(b"\x00\xff\xfe binary\n")
    (templates_dir / "posts" / "link.md").symlink_to(Path("..") / "shared" / "b.md")
    (tmp_path / "doc_link.md").symlink_to(templates_dir / "doc.md")
    link(templates_dir / "doc.md", tmp_path / "doc_hard.md")
    (tmp_path / "out").mkdir()
    link(templates_dir / "shared" / "b.md", tmp_path / "out" / "a.md")
    (tmp_path / "out_later").mkdir()
    link(templates_dir / "posts" / "a.md", tmp_path / "out_later" / "link.md")

    # A different case or normalisation only names the same file on file systems that
    # are insensitive to it, like macOS's default APFS.
    if not exists(realpath(tmp_path / destination)):
        skip(f"'{destination}' names a different file on this file system")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    before = snapshot(tmp_path)

    expect = (
        f"Destination '{tmp_path / expect_destination}' is the same file as "
        f"template '{platen.templates_dir / expect_template}'"
    )

    with raises(DestinationIsTemplateError, match=escape(expect)) as ex:
        press(template, tmp_path / destination)

    assert ex.value.destination_path == tmp_path / expect_destination
    assert ex.value.template_path == platen.templates_dir / expect_template
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("directory", "destination", "expect_directory", "expect_destination"),
    [
        (None, "templates", "templates", "templates"),
        (None, "templates/build", "templates", "templates/build"),
        ("posts", "templates/posts/build", "templates/posts", "templates/posts/build"),
        (None, "templates/a.md", "templates", "templates/a.md"),
        (None, "alias/build", "templates", "alias/build"),
        (None, "TEMPLATES/build", "templates", "TEMPLATES/build"),
        (None, "linked_build", "templates", "linked_build/other/c.md"),
        (None, "dangling_build", "templates", "dangling_build/a.md"),
        (None, ".", "templates", "templates/x.md"),
        (None, "self_build", "templates", "self_build/a.md"),
        (None, "template_build", "templates", "template_build/a.md"),
    ],
    ids=[
        "same directory",
        "within, though ignored",
        "within subdirectory",
        "existing file within",
        "through symlink to directory",
        "different case",
        "through symlink within destination",
        "through dangling symlink within destination",
        "into parent, through directory with the same name",
        "symlink to the directory itself",
        "symlink to a template within the directory",
    ],
)
def test_press_raises_when_destination_is_within_pressed_directory(
    directory: str | None,
    destination: str,
    expect_directory: str,
    expect_destination: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press` and `press_directory` must raise `DestinationWithinDirectoryError` when a
    destination is within the directory being pressed, wherever symlinks lead, and
    nothing must change.

    A press must never write within the directory that it presses, even when a
    `.platenignore` file ignores the destination. The error must name the destination
    absolutely, but as it was given.
    """
    templates_dir = tmp_path / "templates"

    # Each template renders to something other than itself, so that an overwrite would
    # change it.
    for rel_path in ("a.md", "other/c.md", "posts/b.md", "templates/x.md"):
        (templates_dir / rel_path).parent.mkdir(exist_ok=True, parents=True)
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    (templates_dir / ".platenignore").write_text("/build/\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(templates_dir, target_is_directory=True)
    (tmp_path / "linked_build").mkdir()
    (tmp_path / "linked_build" / "other").symlink_to(
        templates_dir / "posts",
        target_is_directory=True,
    )
    (tmp_path / "dangling_build").mkdir()
    (tmp_path / "dangling_build" / "a.md").symlink_to(templates_dir / "new.md")
    (tmp_path / "self_build").mkdir()
    (tmp_path / "self_build" / "a.md").symlink_to(
        templates_dir,
        target_is_directory=True,
    )

    # A destination that's both within the directory and a template must be reported as
    # within the directory.
    (tmp_path / "template_build").mkdir()
    (tmp_path / "template_build" / "a.md").symlink_to(templates_dir / "a.md")

    # A different case only names the same directory on case-insensitive file systems,
    # like macOS's default APFS.
    if not (tmp_path / destination).parent.exists():
        skip(f"'{destination}' names a different directory on this file system")

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    before = snapshot(tmp_path)

    expect = (
        f"Destination '{tmp_path / expect_destination}' is within "
        f"'{tmp_path / expect_directory}', the directory being pressed"
    )

    with raises(DestinationWithinDirectoryError, match=escape(expect)) as ex:
        if directory is None:
            platen.press(destination)
        else:
            platen.press_directory(directory, destination)

    assert ex.value.destination_path == tmp_path / expect_destination
    assert ex.value.directory == tmp_path / expect_directory
    assert snapshot(tmp_path) == before


def test_press_reads_empty_path_object_as_working_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press` must read `Path("")` as the working directory.

    `pathlib` makes `Path("")` into `Path(".")` before Platen sees it, so it can't be
    refused like an empty string can.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    platen = Platen(
        templates_dir,
        {},
    )

    platen.press(Path(""))

    assert (working_dir / "doc.md").read_text(encoding="utf-8") == "Hello, world!\n"


def test_press_warns_when_everything_is_ignored(
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must log a warning when the whole templates directory holds
    nothing to press.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    (templates_dir / ".platenignore").write_text("*.md\n", encoding="utf-8")
    (templates_dir / "file.md").write_text("", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"):
        platen.press(output_dir)

    expect = (
        f"Nothing to press in {platen.templates_dir}: it's empty, or "
        "everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
    assert not output_dir.exists()


def test_press_writes_nothing_when_template_fails_to_render(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must write nothing when any template in a directory fails to render, even
    when other templates render.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "z.md").write_text("{{ missing }}\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(UndefinedError):
        platen.press(output_dir)

    assert not output_dir.exists()


def test_press_writes_nothing_when_walk_meets_broken_symlink(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must write nothing when a directory holds a broken symlink, even when other
    templates render.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "broken.md").symlink_to(templates_dir / "missing.md")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(FileNotFoundError):
        platen.press(output_dir)

    assert not output_dir.exists()


@mark.parametrize(
    ("method", "source", "destination", "expect", "stray"),
    [
        (
            "press_directory",
            "posts",
            "missing/../out",
            [("out/b.md", "b.md"), ("out/c.bin", "c.bin")],
            "missing",
        ),
        (
            "press_directory",
            "posts",
            "templates/posts/new/../../out",
            [("templates/out/b.md", "b.md"), ("templates/out/c.bin", "c.bin")],
            "templates/posts/new",
        ),
        (
            "press_file",
            "posts/b.md",
            "missing/../out.md",
            [("out.md", "b.md")],
            "missing",
        ),
        (
            "press_file",
            "posts/c.bin",
            "missing/../out.bin",
            [("out.bin", "c.bin")],
            "missing",
        ),
    ],
    ids=[
        "directory via parent of missing directory",
        "directory via missing directory within pressed directory",
        "file via parent of missing directory",
        "binary file via parent of missing directory",
    ],
)
def test_press_writes_where_destination_leads(
    method: str,
    source: str,
    destination: str,
    expect: list[tuple[str, str]],
    stray: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must write where a destination leads, with each
    template's permissions, and mustn't leave behind the missing directories that a
    ".." steps up out of.

    Those directories could otherwise be left within the directory being pressed.
    """
    templates_dir = tmp_path / "templates"
    posts_dir = templates_dir / "posts"
    posts_dir.mkdir(parents=True)

    blob = b"\x00\xff\xfe binary\n"
    (posts_dir / "b.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (posts_dir / "b.md").chmod(0o640)
    (posts_dir / "c.bin").write_bytes(blob)
    (posts_dir / "c.bin").chmod(0o750)

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    if method == "press_file":
        platen.press_file(source, destination)
    else:
        platen.press_directory(source, destination)

    for result, name in expect:
        pressed = tmp_path / result
        template = posts_dir / name

        assert pressed.read_bytes() == (b"Hello\n" if name == "b.md" else blob)
        assert S_IMODE(pressed.stat().st_mode) == S_IMODE(template.stat().st_mode)

    assert not (tmp_path / stray).exists()


@mark.parametrize(
    "template",
    [
        "../doc.md",
        "foo/../../doc.md",
    ],
    ids=[
        "parent",
        "parent via subdirectory",
    ],
)
def test_relative_template_is_within_templates_dir(
    template: str,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    Platen must not press templates outside the templates directory,
    even when a relative path reaches them through `..`.
    """
    platen = Platen(
        templates_dir,
        {},
    )

    with raises(TemplateNotInDirectoryError) as ex:
        platen.press_file(template, output_dir / "doc.md")

    assert ex.value.template_path == templates_dir.parent / "doc.md"


@mark.parametrize(
    "template",
    [
        "outside.md",
        "outside_dir/doc.md",
    ],
    ids=[
        "symlink to file",
        "within symlink to directory",
    ],
)
def test_symlinked_template_is_within_templates_dir(
    template: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    Platen must not press an explicitly named template outside the templates
    directory, even when a symlink within the templates directory leads to it.
    """
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "outside.md").symlink_to(outside_dir / "doc.md")
    (templates_dir / "outside_dir").symlink_to(outside_dir, target_is_directory=True)

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(TemplateNotInDirectoryError) as ex:
        platen.press_file(template, output_dir / "doc.md")

    assert ex.value.template_path == (outside_dir / "doc.md").resolve()
    assert not output_dir.exists()


@mark.parametrize(
    "method",
    ["press_file", "press_directory"],
)
def test_template_is_within_templates_dir(
    method: str,
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    Platen must not press templates outside the templates directory.

    Only templates within the templates directory can be pressed,
    because referenced templates (`extends`, `include`, `import`, etc)
    are resolved relative to that directory.
    """
    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    template = templates_dir.parent / "doc.md"

    expect = (
        f"Template '{template}' is not within the templates directory "
        f"'{platen.templates_dir}'"
    )

    with raises(
        TemplateNotInDirectoryError,
        match=escape(expect),
    ) as ex:
        press(template, output_dir / "doc.md")

    assert ex.value.template_path == template
    assert ex.value.templates_dir == platen.templates_dir


def test_templates_dir_is_path(
    templates_dir: Path,
) -> None:
    """
    `templates_dir` must be the path to the templates directory.

    The directory can be specified as a string, but it must be exposed
    here as a `pathlib.Path`.
    """
    platen = Platen(
        templates_dir.as_posix(),
        {},
    )

    assert platen.templates_dir == templates_dir
