import os
from pathlib import Path
from stat import S_IMODE

from jinja2 import TemplateNotFound
from pytest import MonkeyPatch, mark, raises, skip

from platen import (
    Platen,
    TemplateNotPressableError,
)
from tests.platen.symlinks import symlinked_directories


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
    expect = [output_dir / "doc.md"]

    assert pressed == expect

    result = pressed[0].read_text(encoding="utf-8")

    assert result == "Right\n"


@mark.parametrize(
    ("body", "expect"),
    [
        ('A{% include "partials" ignore missing %}B\n', "AB\n"),
        ('A{% include ["partials", "_b.txt"] %}B\n', "AbB\n"),
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

    result = (output_dir / "page.md").read_text(encoding="utf-8")

    assert result == expect


@mark.parametrize(
    ("template", "expect"),
    [
        # `.platenignore` files are left out of walks, but one can still be named.
        (".platenignore", "ignored.md\nignored_dir/\n"),
        ("ignored.md", "Hello, world!\n"),
        ("ignored_dir/file.md", "Hello, world!\n"),
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
    expect_paths = [output_dir / template]

    assert pressed == expect_paths

    result = pressed[0].read_text(encoding="utf-8")

    assert result == expect


@mark.parametrize(
    "template",
    [
        "linked/file.md",
        # Steps up out of a directory within the symlink, back to the symlink's target.
        "linked/sub/../file.md",
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
    expect = [output_dir / "file.md"]

    assert pressed == expect

    result = pressed[0].read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


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

    result = destination.read_text(encoding="utf-8")

    assert result == "Hello\n"


@mark.parametrize(
    ("destination", "expect"),
    [
        # Within the templates directory, which is allowed so long as the destination
        # isn't a template.
        ("source/renamed.md", "source/renamed.md"),
        ("build/new/deeper/file.md", "build/new/deeper/file.md"),
        # Steps up out of `build`, which doesn't exist yet, so the press must create it
        # before the path leads anywhere.
        ("build/../elsewhere/file.md", "elsewhere/file.md"),
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
    result = pressed.read_bytes()

    assert result == b"Hello\n"


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

    result = S_IMODE(device.stat().st_mode)

    assert result == before


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
        result = pressed.read_text(encoding="utf-8")

        assert result == expect

        result = S_IMODE(pressed.stat().st_mode)

        assert result == 0o640

        result = template.read_bytes()

        assert result == template_body


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
        # A symlink to a directory, which is named by where it leads.
        ("linked", TemplateNotPressableError, "real"),
        # A directory within a symlink, which is named by where the symlink leads.
        ("linked/sub", TemplateNotPressableError, "real/sub"),
        # A FIFO, which would block a read until something writes to it.
        ("pipe", TemplateNotPressableError, "pipe"),
        # A broken symlink, named by where it leads, though nothing's there to find.
        ("broken", TemplateNotFound, "nowhere"),
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
    templates_dir = symlinked_directories(tmp_path)
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

        result = str(ex.value)

        assert result == (
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
        result = S_IMODE(pressed.stat().st_mode)

        assert result == mode


@mark.parametrize(
    "body",
    [
        b"{{ greeting }},\nworld!\n",
        b"{{ greeting }},\r\nworld!\r\n",
        b"{{ greeting }},\rworld!\r",
        b"{{ greeting }},\r\nworld!\r",
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

    result = (output_dir / "doc.md").read_bytes()

    assert result == b"Hello,\nworld!\n"
