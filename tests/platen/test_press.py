from logging import WARNING
from pathlib import Path

from jinja2 import UndefinedError
from pytest import LogCaptureFixture, MonkeyPatch, mark, raises, skip
from rootle.testing import (
    can_chmod_deny_listing,
    is_treated_as_same_name,
)

from platen import (
    Platen,
    TemplateNotPressableError,
)


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

    result = platen.press(output_dir)

    assert result == 2

    pressed = output_dir / ".platenignore"
    result = pressed.read_text(encoding="utf-8")

    assert result == "!.platenignore\n"

    result = (output_dir / "doc.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


@mark.parametrize(
    ("method", "template", "destination", "expect"),
    [
        ("press_directory", "SOURCE", ".", ["file.md", "sub/file.md"]),
        ("press_directory", "SOURCE/SUB", ".", ["file.md"]),
        ("press_file", "SOURCE/SUB/file.md", "file.md", ["file.md"]),
    ],
)
def test_press_presses_templates_named_absolutely_in_different_case(
    method: str,
    template: str,
    destination: str,
    expect: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press_file` and `press_directory` must press templates named by absolute paths in a
    different case from how they're stored, on a file system that's insensitive to case,
    like macOS's default APFS.
    """
    if not is_treated_as_same_name(tmp_path, "source", "SOURCE"):
        skip("'SOURCE' names a different directory on this file system")

    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (templates_dir / "sub" / "file.md").write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    press = platen.press_file if method == "press_file" else platen.press_directory
    press(tmp_path / template, output_dir / destination)

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == expect


@mark.parametrize(
    "page",
    [
        # No page references it, so `_legacy.txt` is walked and pressed itself.
        None,
        '{% extends "_legacy.txt" %}\n',
        '{% import "_legacy.txt" as legacy %}\n',
        '{% include "_legacy.txt" %}\n',
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

    expect = platen.templates_dir / "_legacy.txt"

    assert ex.value.template_path == expect
    assert ex.value.reason == (
        "it isn't valid UTF-8 (invalid continuation byte at byte 3)"
    )
    assert not output_dir.exists()


@mark.parametrize(
    "destination",
    [
        "",
        Path(""),
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

    result = (working_dir / "doc.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


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

    result = platen.press(output_dir)

    assert result == 1

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
    if not can_chmod_deny_listing(tmp_path):
        skip("Permissions don't stop this user from reading directories")

    templates_dir = tmp_path / "source"
    directory = templates_dir / "z"
    directory.mkdir(parents=True)
    (templates_dir / "a.md").write_text("Hello, world!\n", encoding="utf-8")
    (directory / "doc.md").write_text("Hello, world!\n", encoding="utf-8")
    directory.chmod(0o000)

    try:
        platen = Platen(
            templates_dir,
            {},
        )

        with raises(PermissionError) as ex:
            platen.press(output_dir)

        expect = str(platen.templates_dir / "z")

        assert ex.value.filename == expect
    finally:
        directory.chmod(0o755)

    assert not output_dir.exists()
