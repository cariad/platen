import os
from errno import ELOOP
from logging import WARNING
from os import R_OK, W_OK, access, link
from os.path import exists, realpath
from pathlib import Path
from re import escape
from stat import S_IMODE

from jinja2 import TemplateNotFound, UndefinedError
from pytest import LogCaptureFixture, MonkeyPatch, mark, raises, skip

from platen import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    Platen,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from tests.snapshots import snapshot


def _symlinked_directories(tmp_path: Path) -> Path:
    """
    Create a templates directory that holds `linked`, a symlink to a directory that
    holds another symlink to a directory, and return the templates directory's path.

    The inner symlink is ignored, so that a walk of the directory that holds it can
    press everything else.
    """
    templates_dir = tmp_path / "source"
    real_dir = templates_dir / "real"
    (real_dir / "sub" / "deeper").mkdir(parents=True)
    (templates_dir / "dir").mkdir()
    (templates_dir / ".platenignore").write_text("sublinked\n", encoding="utf-8")

    for rel_path in ("file.md", "sub/file.md", "sub/deeper/file.md"):
        (real_dir / rel_path).write_text("Hello, world!\n", encoding="utf-8")

    (real_dir / "sublinked").symlink_to(real_dir / "sub", target_is_directory=True)
    (templates_dir / "linked").symlink_to(real_dir, target_is_directory=True)
    return templates_dir


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

    assert ex.value.destination_path == destination / "a.md"
    assert ex.value.template_path == platen.templates_dir / "posts" / "a.md"


def test_press_directory_presses_directory_named_in_different_case(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_directory` must press a directory named in a different case from how it's
    stored, on a file system that's insensitive to case, like macOS's default APFS, and
    no other directory that starts with the same name.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "subway").mkdir()
    (templates_dir / "sub" / "a.md").write_text("{{ greeting }}\n", encoding="utf-8")
    (templates_dir / "subway" / "b.md").write_text("{{ greeting }}\n", encoding="utf-8")

    if not exists(templates_dir / "SUB"):
        skip("'SUB' names a different directory on this file system")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    assert platen.press_directory("SUB", output_dir) == 1

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "a.md"]
    assert pressed[0].read_text(encoding="utf-8") == "Hello\n"


@mark.parametrize(
    ("directory", "expect"),
    [
        ("linked", ["file.md", "sub/deeper/file.md", "sub/file.md"]),
        ("linked/sub", ["deeper/file.md", "file.md"]),
        ("linked/sublinked", ["deeper/file.md", "file.md"]),
        ("linked/sublinked/deeper", ["file.md"]),
        ("dir/../linked", ["file.md", "sub/deeper/file.md", "sub/file.md"]),
        ("linked/sub/../sub", ["deeper/file.md", "file.md"]),
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
        _symlinked_directories(tmp_path),
        {},
    )

    assert platen.press_directory(directory, output_dir) == len(expect)

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == expect


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

    assert ex.value.destination_path == destination / "b.md"
    assert ex.value.template_path == platen.templates_dir / "posts" / "a.md"
    assert (destination / "a.md").read_text(encoding="utf-8") == "Hello\n"

    template = templates_dir / "posts" / "a.md"
    assert template.read_text(encoding="utf-8") == "{{ greeting }}\n"


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

    assert ex.value.destination_path == templates_dir / "shared" / "footer.md"
    assert ex.value.template_path == platen.templates_dir / "shared" / "footer.md"
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

    assert ex.value.destination_path == destination / "link"
    assert ex.value.template_path == platen.templates_dir / "sub" / "link"
    assert snapshot(tmp_path) == before


@mark.parametrize(
    ("directory", "expect", "expect_path"),
    [
        ("doc.md", NotADirectoryError, "doc.md"),
        ("broken", FileNotFoundError, "nowhere"),
        ("missing", FileNotFoundError, "missing"),
    ],
    ids=[
        "file",
        "broken symlink",
        "missing",
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

    platen = Platen(
        templates_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"), raises(expect) as ex:
        platen.press_directory(directory, output_dir)

    assert ex.value.filename == str(platen.templates_dir / expect_path)
    assert not caplog.records
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
    ("body", "expect"),
    [
        ('A{% include "partials" ignore missing %}B\n', "AB\n"),
        ('A{% include ["partials", "_b.txt"] %}B\n', "AbB\n"),
    ],
    ids=[
        "ignore missing",
        "list of templates",
    ],
)
def test_press_file_lets_jinja_skip_reference_that_is_not_a_file(
    body: str,
    expect: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must leave a referenced template that isn't a regular file, like a
    directory, to Jinja, so that `ignore missing` and lists of templates still skip it.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "partials").mkdir(parents=True)
    (templates_dir / "_b.txt").write_text("b", encoding="utf-8")
    (templates_dir / "page.md").write_text(body, encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    platen.press_file("page.md", output_dir / "page.md")

    assert (output_dir / "page.md").read_text(encoding="utf-8") == expect


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
    ("destination", "expect"),
    [
        ("source/renamed.md", "source/renamed.md"),
        ("build/new/deeper/file.md", "build/new/deeper/file.md"),
        ("build/../elsewhere/file.md", "elsewhere/file.md"),
    ],
    ids=[
        "renamed within templates directory",
        "within new directories",
        "via parent",
    ],
)
def test_press_file_presses_to_destination(
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

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_file("file.md", destination)

    pressed = tmp_path / expect
    assert pressed.read_bytes() == b"Hello\n"


def test_press_file_presses_to_device_without_changing_its_permissions(
    tmp_path: Path,
) -> None:
    """
    `press_file` must press a template to a device like `/dev/null` without giving the
    device its template's permissions.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    template = templates_dir / "file"
    template.write_text("{{ greeting }}, world!\n", encoding="utf-8")
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
    `press_file` must raise Jinja's `TemplateNotFound` when its template doesn't exist,
    rather than pressing nothing, and write nothing.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(TemplateNotFound) as ex:
        platen.press_file("missing.md", output_dir / "missing.md")

    assert ex.value.name == "missing.md"
    assert not output_dir.exists()


@mark.parametrize(
    ("template", "expect", "expect_name"),
    [
        (".", TemplateNotPressableError, "."),
        ("dir", TemplateNotPressableError, "dir"),
        ("linked", TemplateNotPressableError, "real"),
        ("linked/sub", TemplateNotPressableError, "real/sub"),
        ("pipe", TemplateNotPressableError, "pipe"),
        ("broken", TemplateNotFound, "nowhere"),
    ],
    ids=[
        "templates directory",
        "directory",
        "symlink to directory",
        "directory within symlink",
        "fifo",
        "broken symlink",
    ],
)
def test_press_file_raises_when_template_is_not_regular_file(
    template: str,
    expect: type[TemplateNotFound | TemplateNotPressableError],
    expect_name: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must raise `TemplateNotPressableError` when its template isn't a
    regular file, like a directory or a FIFO, rather than pressing what's within it or
    blocking on it, and write nothing.

    A template named through a symlink must be named by where the symlink leads. A
    broken symlink leads to nothing at all, so it raises `TemplateNotFound` instead.
    """
    templates_dir = _symlinked_directories(tmp_path)
    (templates_dir / "broken").symlink_to(templates_dir / "nowhere")

    if template == "pipe":
        # Not every platform has FIFOs, like Windows.
        if not hasattr(os, "mkfifo"):
            skip("This platform doesn't have FIFOs")

        os.mkfifo(templates_dir / "pipe")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(expect) as ex:
        platen.press_file(template, output_dir / "file.md")

    if isinstance(ex.value, TemplateNotFound):
        assert ex.value.name == expect_name
    else:
        path = platen.templates_dir / expect_name
        assert ex.value.template_path == path
        assert ex.value.reason == "it isn't a regular file"

        assert str(ex.value) == (
            f"Template '{path}' can't be pressed: it isn't a regular file"
        )

    assert not output_dir.exists()


def test_press_file_updates_permissions(
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
    template.write_text("echo hello\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    pressed = output_dir / "script.sh"

    for mode in (0o755, 0o644, 0o700):
        template.chmod(mode)
        platen.press_file("script.sh", pressed)
        assert S_IMODE(pressed.stat().st_mode) == mode


@mark.parametrize(
    "body",
    [
        b"{{ greeting }},\nworld!\n",
        b"{{ greeting }},\r\nworld!\r\n",
        b"{{ greeting }},\rworld!\r",
        b"{{ greeting }},\r\nworld!\r",
    ],
    ids=[
        "LF",
        "CRLF",
        "CR",
        "mixed",
    ],
)
def test_press_file_writes_line_endings_as_jinja_renders_them(
    body: bytes,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` must write exactly what Jinja renders, without translating its line
    endings.

    Jinja ends every line with a line feed, whatever the template's own line endings.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_bytes(body)

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    platen.press_file("doc.md", output_dir / "doc.md")

    assert (output_dir / "doc.md").read_bytes() == b"Hello,\nworld!\n"


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


def test_press_presses_ignore_file_that_is_re_included(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must press a `.platenignore` file when a `.platenignore` file re-includes
    it, and count it with the other templates.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / ".platenignore").write_text("!.platenignore\n", encoding="utf-8")
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    assert platen.press(output_dir) == 2

    pressed = output_dir / ".platenignore"
    assert pressed.read_text(encoding="utf-8") == "!.platenignore\n"
    assert (output_dir / "doc.md").read_text(encoding="utf-8") == "Hello, world!\n"


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
    ("method", "template", "destination"),
    [
        ("press_directory", "links", "links"),
        ("press_file", "links/file.md", "links/file.md"),
        ("press_file", "links/../links/file.md", "links/file.md"),
    ],
    ids=["walked", "named", "named via parent"],
)
def test_press_presses_symlink_to_file_with_target_content(
    method: str,
    template: str,
    destination: str,
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

    (templates_dir / "file.md").write_text(
        "{{ greeting }}, world!\n",
        encoding="utf-8",
    )

    (links_dir / "file.md").symlink_to(Path("..") / "file.md")

    platen = Platen(
        templates_dir,
        {"greeting": "Hello"},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    press(template, output_dir / destination)

    pressed = output_dir / "links" / "file.md"

    assert not pressed.is_symlink()
    assert pressed.read_text(encoding="utf-8") == "Hello, world!\n"
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


def test_press_protects_relative_path_from_construction_working_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    A relative protected path must stay relative to the working directory that Platen
    was constructed in, even when the working directory changes before a press.
    """
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    values = tmp_path / "values.yaml"
    values.write_text("greeting: Hello\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {},
        protect=["values.yaml"],
    )

    monkeypatch.chdir(tmp_path / "build")

    with raises(DestinationIsProtectedError) as ex:
        platen.press_file("doc.md", values)

    assert ex.value.protected_path == values
    assert values.read_text(encoding="utf-8") == "greeting: Hello\n"


def test_press_raises_for_destination_that_cannot_be_written(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must name a destination that can't be written as it was given, even when a
    symlink within the destination leads elsewhere, like every other error about it.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "sub" / "page.md").write_text("Page\n", encoding="utf-8")
    directory = tmp_path / "ro"
    directory.mkdir()
    output_dir.mkdir()
    (output_dir / "sub").symlink_to(directory)
    directory.chmod(0o555)

    try:
        if access(directory, W_OK):
            skip("Permissions don't stop this user from writing files")

        platen = Platen(
            templates_dir,
            {},
        )

        with raises(PermissionError) as ex:
            platen.press(output_dir)

        assert ex.value.filename == str(output_dir / "sub" / "page.md")
    finally:
        directory.chmod(0o755)


@mark.parametrize(
    "method",
    [
        "press_file",
        "press_directory",
        "press",
    ],
    ids=[
        "press_file",
        "press_directory",
        "press, walked",
    ],
)
def test_press_raises_for_symlink_loop(
    method: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file`, `press_directory` and `press` must raise the same `OSError` for a loop
    of symlinks on every version of Python, rather than `RuntimeError` on some, and
    nothing must be written.

    Every template is identified before the first is pressed, so a walked loop must be
    raised before the templates that sort before it are pressed.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "loop").symlink_to("loop")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(OSError) as ex:
        if method == "press":
            platen.press(output_dir)
        elif method == "press_directory":
            platen.press_directory("loop", output_dir)
        else:
            platen.press_file("loop", output_dir)

    assert type(ex.value) is OSError
    assert ex.value.errno == ELOOP
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


@mark.parametrize(
    "page",
    [
        None,
        '{% extends "_legacy.txt" %}\n',
        '{% import "_legacy.txt" as legacy %}\n',
        '{% include "_legacy.txt" %}\n',
    ],
    ids=[
        "pressed",
        "extended",
        "imported",
        "included",
    ],
)
def test_press_raises_for_template_that_is_not_utf8(
    page: str | None,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must refuse a template that isn't UTF-8, naming it and the problem, and
    write nothing, whether it's pressed or only referenced by another template.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    # This sorts after the template that fails, so the press stops before it.
    (templates_dir / "z.md").write_text("Z\n", encoding="utf-8")
    (templates_dir / "_legacy.txt").write_bytes(b"Caf\xe9\n")

    if page is not None:
        # Ignore the template, so that it's only loaded through the reference.
        (templates_dir / ".platenignore").write_text("_legacy.txt\n", encoding="utf-8")
        (templates_dir / "page.md").write_text(page, encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(TemplateNotPressableError) as ex:
        platen.press(output_dir)

    assert ex.value.template_path == platen.templates_dir / "_legacy.txt"
    assert ex.value.reason == (
        "it isn't valid UTF-8 (invalid continuation byte at byte 3)"
    )
    assert not output_dir.exists()


@mark.parametrize(
    ("target", "expect"),
    [
        ("dir", TemplateNotPressableError),
        ("/dev/null", TemplateNotPressableError),
        ("fifo", TemplateNotPressableError),
        ("nowhere", TemplateNotFound),
    ],
    ids=[
        "directory",
        "device",
        "fifo",
        "broken",
    ],
)
def test_press_raises_for_walked_symlink_that_is_not_a_file(
    target: str,
    expect: type[TemplateNotFound | TemplateNotPressableError],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must raise when a walked symlink leads to anything but a regular file,
    rather than reading a directory, blocking on a FIFO or reading a device forever.

    A symlink that leads to something that isn't a regular file raises
    `TemplateNotPressableError`, and a broken symlink raises `TemplateNotFound`. Either
    way, the templates that sort before it stay pressed.
    """
    (tmp_path / "dir").mkdir()

    if target == "fifo":
        # Not every platform has FIFOs, like Windows.
        if not hasattr(os, "mkfifo"):
            skip("This platform doesn't have FIFOs")

        os.mkfifo(tmp_path / target)
    elif target == "/dev/null" and not exists(target):
        skip(f"This platform doesn't have {target}")

    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "a.md").write_text("A\n", encoding="utf-8")

    # An absolute target, like "/dev/null", replaces `tmp_path`.
    (templates_dir / "link").symlink_to(tmp_path / target)

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(expect) as ex:
        platen.press(output_dir)

    if isinstance(ex.value, TemplateNotFound):
        assert ex.value.name == "link"
    else:
        assert ex.value.template_path == platen.templates_dir / "link"
        assert ex.value.reason == "it isn't a regular file"

    assert [p.name for p in output_dir.iterdir()] == ["a.md"]
    assert (output_dir / "a.md").read_text(encoding="utf-8") == "A\n"


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
        ("press_file", "doc.md", "doc_link.md", "doc.md", "doc_link.md"),
        ("press_file", "doc.md", "doc_hard.md", "doc.md", "doc_hard.md"),
        ("press_directory", "posts", "out", "posts/link.md", "out/a.md"),
        ("press_file", "doc.md", "templates/DOC.md", "doc.md", "templates/DOC.md"),
        (
            "press_file",
            "café.md",
            "templates/café.md",
            "café.md",
            "templates/café.md",
        ),
    ],
    ids=[
        "same path",
        "via parent",
        "via parent of missing directory",
        "symlink to template",
        "hard link to template",
        "hard link to another template",
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

    Every template is identified before the first is pressed, so a destination that's a
    template yet to be pressed must be refused too.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)
    (templates_dir / "shared").mkdir()

    # Each template renders to something other than itself, so that an overwrite would
    # change it.
    for rel_path in ("doc.md", "café.md", "posts/a.md", "shared/b.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    (templates_dir / "posts" / "link.md").symlink_to(Path("..") / "shared" / "b.md")
    (tmp_path / "doc_link.md").symlink_to(templates_dir / "doc.md")
    link(templates_dir / "doc.md", tmp_path / "doc_hard.md")
    (tmp_path / "out").mkdir()
    link(templates_dir / "shared" / "b.md", tmp_path / "out" / "a.md")

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
    ("directory", "destination", "expect_directory"),
    [
        (None, "templates", "templates"),
        (None, "templates/build", "templates"),
        ("posts", "templates/posts/build", "templates/posts"),
        (None, "templates/a.md", "templates"),
        (None, "alias/build", "templates"),
        (None, "TEMPLATES/build", "templates"),
    ],
    ids=[
        "same directory",
        "within, though ignored",
        "within subdirectory",
        "existing file within",
        "through symlink to directory",
        "different case",
    ],
)
def test_press_raises_when_destination_is_within_pressed_directory(
    directory: str | None,
    destination: str,
    expect_directory: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press` and `press_directory` must raise `DestinationWithinDirectoryError` when the
    destination is within the directory being pressed, wherever symlinks lead, and
    nothing must change.

    A press must never write within the directory that it presses, even when a
    `.platenignore` file ignores the destination. The error must name the destination
    as it was given.
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "posts").mkdir(parents=True)

    # Each template renders to something other than itself, so that an overwrite would
    # change it.
    for rel_path in ("a.md", "posts/b.md"):
        (templates_dir / rel_path).write_text("{{ greeting }}\n", encoding="utf-8")

    (templates_dir / ".platenignore").write_text("/build/\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(templates_dir, target_is_directory=True)

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
        f"Destination '{destination}' is within "
        f"'{tmp_path / expect_directory}', the directory being pressed"
    )

    with raises(DestinationWithinDirectoryError, match=escape(expect)) as ex:
        if directory is None:
            platen.press(destination)
        else:
            platen.press_directory(directory, destination)

    assert ex.value.destination_path == Path(destination)
    assert ex.value.directory == tmp_path / expect_directory
    assert snapshot(tmp_path) == before


@mark.parametrize(
    "destination",
    [
        "",
        Path(""),
    ],
    ids=[
        "string",
        "path",
    ],
)
def test_press_reads_empty_path_as_working_directory(
    destination: Path | str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `press` must read an empty path as the working directory, whether it's a string or
    a `Path`, like `pathlib` does.
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

    platen.press(destination)

    assert (working_dir / "doc.md").read_text(encoding="utf-8") == "Hello, world!\n"


def test_press_skips_binary_files_by_name(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must skip common binary files by their names, in lower or upper case,
    rather than pressing them as templates.

    Only the name counts, so a file with a binary file's name is skipped even when it
    holds text.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    (templates_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\x00\x00")

    # Rendering this template would raise `UndefinedError`.
    (templates_dir / "sub" / "SCAN.PDF").write_text(
        "{{ undefined }}\n",
        encoding="utf-8",
    )

    platen = Platen(
        templates_dir,
        {},
    )

    assert platen.press(output_dir) == 1

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == ["doc.md"]


def test_press_stops_at_first_template_that_fails_to_render(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must stop at the first template that fails to render, in walk order, so
    that the templates before it stay pressed and none after it are pressed.

    A directory sorts by its name with a trailing separator, so `b.md` comes before
    `b/c.md`.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "b").mkdir(parents=True)
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "b.md").write_text("{{ missing }}\n", encoding="utf-8")
    (templates_dir / "b" / "c.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(UndefinedError):
        platen.press(output_dir)

    pressed = sorted(
        p.relative_to(output_dir).as_posix() for p in output_dir.rglob("*")
    )
    assert pressed == ["a.md"]


def test_press_warns_when_everything_is_ignored(
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must log a warning and return 0 when the whole templates directory holds
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
        pressed = platen.press(output_dir)

    assert pressed == 0

    expect = (
        f"Nothing to press in {platen.templates_dir}: it's empty, or "
        "everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
    assert not output_dir.exists()


def test_press_writes_nothing_when_walk_fails(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must walk the whole directory before it presses anything, so that a
    directory that can't be walked stops the press before any template is pressed,
    even those that sort before it.
    """
    templates_dir = tmp_path / "source"
    directory = templates_dir / "z"
    directory.mkdir(parents=True)
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (directory / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    directory.chmod(0o000)

    try:
        if access(directory, R_OK):
            skip("Permissions don't stop this user from reading directories")

        platen = Platen(
            templates_dir,
            {},
        )

        with raises(PermissionError) as ex:
            platen.press(output_dir)

        assert ex.value.filename == str(platen.templates_dir / "z")
    finally:
        directory.chmod(0o755)

    assert not output_dir.exists()


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
    ("method", "template", "expect"),
    [
        ("press_file", "outside.md", "doc.md"),
        ("press_file", "outside_dir/doc.md", "doc.md"),
        ("press_directory", "outside_dir", "."),
    ],
    ids=[
        "symlink to file",
        "within symlink to directory",
        "symlink to directory",
    ],
)
def test_symlinked_template_is_within_templates_dir(
    method: str,
    template: str,
    expect: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    Platen must not press an explicitly named template, or directory of templates,
    outside the templates directory, even when a symlink within the templates directory
    leads to it.
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

    press = platen.press_file if method == "press_file" else platen.press_directory

    with raises(TemplateNotInDirectoryError) as ex:
        press(template, output_dir / "doc.md")

    assert ex.value.template_path == (outside_dir / expect).resolve()
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
