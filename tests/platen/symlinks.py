from pathlib import Path


def symlinked_directories(tmp_path: Path) -> Path:
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
