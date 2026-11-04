from pathlib import Path

from pytest import raises

from platen import (
    Platen,
)
from tests.snapshots import snapshot


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


def test_templates_names_every_template_that_press_would_press(
    tmp_path: Path,
) -> None:
    """
    `templates` must name every template that `press` would press, in walk order,
    without writing or loading anything.

    Ignored files, `.platenignore` files and common binary files must be left out, like
    a press leaves them out. A directory sorts by its name with a trailing separator, so
    `b.md` comes before `b/c.md`, and `b/c.md` before `z.md`.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "b").mkdir(parents=True)
    (templates_dir / ".platenignore").write_text("*.log\n", encoding="utf-8")
    (templates_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    for rel_path in ("a.md", "b.md", "b/c.md", "notes.log", "z.md"):
        (templates_dir / rel_path).write_text("Hello, world!\n", encoding="utf-8")

    platen = Platen(
        templates_dir,
        {},
    )

    before = snapshot(tmp_path)

    result = platen.templates()

    assert result == ["a.md", "b.md", "b/c.md", "z.md"]

    expect: frozenset[Path] = frozenset()

    assert platen.loaded == expect

    result = snapshot(tmp_path)

    assert result == before


def test_templates_raises_when_walk_fails(
    tmp_path: Path,
) -> None:
    """
    `templates` must raise the walk's `OSError`, like `FileNotFoundError` when the
    templates directory doesn't exist, rather than naming no templates.
    """
    platen = Platen(
        tmp_path / "missing",
        {},
    )

    with raises(FileNotFoundError) as ex:
        platen.templates()

    expect = str(platen.templates_dir)

    assert ex.value.filename == expect
