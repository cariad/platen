from pathlib import Path

from pytest import MonkeyPatch, raises

from platen import (
    Platen,
)


def test_written_names_every_file_that_press_writes(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    `written` must name every file that a press wrote, as each destination was given,
    even relative to the working directory, and nothing before anything is pressed.
    """
    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)

    for rel_path in ("a.md", "sub/b.md"):
        (templates_dir / rel_path).write_text("Hello, world!\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    platen = Platen(
        templates_dir,
        {},
    )

    expect: frozenset[Path] = frozenset()

    assert platen.written == expect

    result = platen.press("build")

    assert result == 2

    expect = frozenset(
        [
            Path("build") / "a.md",
            Path("build") / "sub" / "b.md",
        ],
    )

    assert platen.written == expect


def test_written_names_file_that_press_fails_to_write(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `written` must name a destination that a press tried to write even when writing it
    failed, because a failed write can leave it half-written.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()
    (templates_dir / "doc.md").write_text("Hello, world!\n", encoding="utf-8")

    # A directory is in the way, so the write fails.
    destination = output_dir / "doc.md"
    destination.mkdir(parents=True)

    platen = Platen(
        templates_dir,
        {},
    )

    with raises(IsADirectoryError):
        platen.press_file("doc.md", destination)

    expect = frozenset([destination])

    assert platen.written == expect
