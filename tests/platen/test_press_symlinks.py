import os
from errno import ELOOP
from os.path import exists
from pathlib import Path

from jinja2 import TemplateNotFound
from pytest import mark, raises, skip

from platen import (
    Platen,
    TemplateNotPressableError,
)
from tests.snapshots import snapshot


@mark.parametrize(
    ("method", "template", "destination"),
    [
        ("press_directory", "links", "links"),
        ("press_file", "links/file.md", "links/file.md"),
        ("press_file", "links/../links/file.md", "links/file.md"),
    ],
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

    result = pressed.read_text(encoding="utf-8")

    assert result == "Hello, world!\n"
    assert not (output_dir / "file.md").exists()


@mark.parametrize(
    ("method", "template", "destination", "expect"),
    [
        ("press_directory", "alias", ".", ["file.md", "sub/file.md"]),
        ("press_directory", "alias/sub", "sub", ["sub/file.md"]),
        ("press_file", "alias/sub/file.md", "sub/file.md", ["sub/file.md"]),
        # Through a second symlink, within the templates directory, that leads to `sub`.
        ("press_file", "alias/linked/file.md", "linked/file.md", ["linked/file.md"]),
        # Named relative to the templates directory, unlike the others, by stepping up
        # out of it and back in through the symlink.
        ("press_file", "../alias/sub/file.md", "sub/file.md", ["sub/file.md"]),
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
    "method",
    [
        "press_file",
        "press_directory",
        # Finds the loop by walking, so must raise it before pressing `a.md`, which
        # sorts before it.
        "press",
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

    result = type(ex.value)

    assert result is OSError
    assert ex.value.errno == ELOOP
    assert not output_dir.exists()


@mark.parametrize(
    ("method", "template", "destination"),
    [
        ("press_file", "doc.md", "loop/doc.md"),
        ("press_directory", ".", "loop"),
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

    result = type(ex.value)

    assert result is OSError
    assert ex.value.errno == ELOOP

    result = snapshot(tmp_path)

    assert result == before


@mark.parametrize(
    ("target", "expect"),
    [
        ("dir", TemplateNotPressableError),
        ("/dev/null", TemplateNotPressableError),
        ("fifo", TemplateNotPressableError),
        # Nothing's made at `nowhere`, so the symlink is broken.
        ("nowhere", TemplateNotFound),
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
        expect_path = platen.templates_dir / "link"

        assert ex.value.template_path == expect_path
        assert ex.value.reason == "it isn't a regular file"

    result = [p.name for p in output_dir.iterdir()]

    assert result == ["a.md"]

    result = (output_dir / "a.md").read_text(encoding="utf-8")

    assert result == "A\n"
