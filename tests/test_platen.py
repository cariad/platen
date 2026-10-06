from logging import WARNING
from pathlib import Path
from re import escape
from stat import S_IMODE

from pytest import LogCaptureFixture, mark, raises

from platen import NestedDirectoriesError, Platen, TemplateNotInDirectoryError


@mark.parametrize(
    ("templates_subdir", "output_subdir"),
    [
        ("dir", "dir"),
        ("dir/templates", "dir"),
        ("dir", "dir/build"),
    ],
    ids=[
        "same directory",
        "templates within output",
        "output within templates",
    ],
)
def test_directories_are_not_nested(
    templates_subdir: str,
    output_subdir: str,
    tmp_path: Path,
) -> None:
    """
    Platen must not allow the templates and output directories to be the
    same, or for either to be nested within the other.
    """
    output_dir = tmp_path / output_subdir
    templates_dir = tmp_path / templates_subdir

    expect = (
        f"Templates directory '{templates_dir}' and output directory "
        f"'{output_dir}' cannot be the same or nested within each other"
    )

    with raises(
        NestedDirectoriesError,
        match=escape(expect),
    ) as ex:
        Platen(
            templates_dir,
            output_dir,
            {},
        )

    assert ex.value.output_dir == output_dir
    assert ex.value.templates_dir == templates_dir


def test_output_dir_is_path(
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `output_dir` must be the path to the output directory.

    The directory can be specified as a string, but it must be exposed
    here as a `pathlib.Path`.
    """
    platen = Platen(
        templates_dir,
        output_dir.as_posix(),
        {},
    )

    assert platen.output_dir == output_dir


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
def test_press_presses_explicit_template_even_if_ignored(
    template: str,
    expect: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must press an explicitly named template, even when a `.platenignore` file
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
        output_dir,
        {"greeting": "Hello"},
    )

    platen.press(template)

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / template]
    assert pressed[0].read_text(encoding="utf-8") == expect


@mark.parametrize(
    "template",
    [
        None,
        "linked",
        "linked/sub",
        "linked/sublinked",
        "linked/sublinked/deeper",
        "dir/../linked",
        "linked/sub/../sub",
    ],
    ids=[
        "walked",
        "named",
        "directory within",
        "symlink within",
        "directory within symlink within",
        "named via parent",
        "directory within via parent",
    ],
)
def test_press_raises_for_symlink_to_directory(
    template: str | None,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must raise `IsADirectoryError` when it's asked to press a symlink to a
    directory or a directory within one, or a directory it's pressing holds one.

    Symlinks are pressed like files and never walked into, so a symlink to a directory
    can't be pressed, and its target mustn't be pressed in its place. The error must
    name the outermost symlink, like a walk would.
    """
    templates_dir = tmp_path / "source"
    real_dir = templates_dir / "real"
    (real_dir / "sub" / "deeper").mkdir(parents=True)
    (templates_dir / "dir").mkdir()
    (real_dir / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "sub" / "file.md").write_text("Hello, world!\n", encoding="utf-8")
    (real_dir / "sublinked").symlink_to(real_dir / "sub", target_is_directory=True)

    link = templates_dir / "linked"
    link.symlink_to(real_dir, target_is_directory=True)

    platen = Platen(
        templates_dir,
        output_dir,
        {},
    )

    with raises(IsADirectoryError) as ex:
        platen.press(template)

    assert ex.value.filename == str(platen.templates_dir / "linked")

    # A walk might press other files before it meets the symlink, but a named template
    # must be refused before anything is pressed.
    if template is not None:
        assert not output_dir.exists()


def test_press_raises_for_missing_template(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must raise `FileNotFoundError` when an explicitly named template doesn't
    exist, rather than pressing nothing.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    platen = Platen(
        templates_dir,
        output_dir,
        {},
    )

    with raises(FileNotFoundError) as ex:
        platen.press("missing.md")

    assert ex.value.filename == str(platen.templates_dir / "missing.md")


@mark.parametrize(
    "templates",
    [
        ["links"],
        ["links/blob.bin", "links/file.md"],
        ["links/../links/blob.bin", "links/../links/file.md"],
    ],
    ids=["walked", "named", "named via parent"],
)
def test_press_presses_symlink_to_file_with_target_content(
    templates: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must press a symlink to a file with its target's content, to the symlink's
    own path, whether it's named or found by walking a directory.

    The pressed file must be a regular file, not a copy of the symlink, and nothing
    must be pressed to the target's path.
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
        output_dir,
        {"greeting": "Hello"},
    )

    for template in templates:
        platen.press(template)

    pressed_blob = output_dir / "links" / "blob.bin"
    pressed_file = output_dir / "links" / "file.md"

    assert not pressed_blob.is_symlink()
    assert not pressed_file.is_symlink()
    assert pressed_blob.read_bytes() == blob
    assert pressed_file.read_text(encoding="utf-8") == "Hello, world!\n"
    assert not (output_dir / "blob.bin").exists()
    assert not (output_dir / "file.md").exists()


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
def test_press_presses_template_within_symlink_to_directory_to_named_path(
    template: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must press an explicitly named template within a symlink to a directory to
    the path it's named by, not to its target's path.
    """
    templates_dir = tmp_path / "source"
    real_dir = templates_dir / "real"
    (real_dir / "sub").mkdir(parents=True)
    (real_dir / "file.md").write_text("{{ greeting }}, world!\n", encoding="utf-8")
    (templates_dir / "linked").symlink_to(real_dir, target_is_directory=True)

    platen = Platen(
        templates_dir,
        output_dir,
        {"greeting": "Hello"},
    )

    platen.press(template)

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "linked" / "file.md"]
    assert pressed[0].read_text(encoding="utf-8") == "Hello, world!\n"


def test_press_follows_symlink_before_parent(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must step up from a symlink with `..` the same way the operating system
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
        output_dir,
        {},
    )

    platen.press("linked/../doc.md")

    pressed = [p for p in output_dir.rglob("*") if p.is_file()]
    assert pressed == [output_dir / "real" / "doc.md"]
    assert pressed[0].read_text(encoding="utf-8") == "Right\n"


@mark.parametrize(
    ("template", "expect"),
    [
        ("alias", ["file.md", "sub/file.md"]),
        ("alias/sub", ["sub/file.md"]),
        ("alias/sub/file.md", ["sub/file.md"]),
        ("alias/linked/file.md", ["linked/file.md"]),
        ("../alias/sub/file.md", ["sub/file.md"]),
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
    template: str,
    expect: list[str],
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must press templates named through a symlink to the templates directory,
    like the symlink that the templates directory was given as.

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
        output_dir,
        {},
    )

    named = Path(template)
    platen.press(named if named.parts[0] == ".." else tmp_path / named)

    pressed = sorted(
        p.relative_to(output_dir).as_posix()
        for p in output_dir.rglob("*")
        if p.is_file()
    )

    assert pressed == expect


@mark.parametrize(
    "template",
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
def test_press_warns_when_directory_has_nothing_to_press(
    template: str,
    caplog: LogCaptureFixture,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must log a warning when a directory holds nothing to press, even when other
    directories do.

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
        output_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"):
        platen.press(template)

    expect = (
        f"Nothing to press in {platen.templates_dir / template}: it's "
        "empty, or everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
    assert not output_dir.exists()


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
        output_dir,
        {},
    )

    with caplog.at_level(WARNING, logger="platen"):
        platen.press()

    expect = (
        f"Nothing to press in {platen.templates_dir}: it's empty, or "
        "everything in it is ignored"
    )

    assert ("platen.platen", WARNING, expect) in caplog.record_tuples
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
def test_press_updates_permissions(
    body: bytes,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must update the pressed file's permissions.

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
        output_dir,
        {},
    )

    pressed = platen.output_dir / "script.sh"

    for mode in (0o755, 0o644, 0o700):
        template.chmod(mode)
        platen.press("script.sh")
        assert S_IMODE(pressed.stat().st_mode) == mode


def test_template_is_within_templates_dir(
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
        output_dir,
        {},
    )

    template = templates_dir.parent / "doc.md"

    expect = (
        f"Template '{template}' is not within the templates directory "
        f"'{platen.templates_dir}'"
    )

    with raises(
        TemplateNotInDirectoryError,
        match=escape(expect),
    ) as ex:
        platen.press(template)

    assert ex.value.template_path == template
    assert ex.value.templates_dir == platen.templates_dir


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
        output_dir,
        {},
    )

    with raises(TemplateNotInDirectoryError) as ex:
        platen.press(template)

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
        output_dir,
        {},
    )

    with raises(TemplateNotInDirectoryError) as ex:
        platen.press(template)

    assert ex.value.template_path == (outside_dir / "doc.md").resolve()
    assert not output_dir.exists()


def test_templates_dir_is_path(
    output_dir: Path,
    templates_dir: Path,
) -> None:
    """
    `templates_dir` must be the path to the templates directory.

    The directory can be specified as a string, but it must be exposed
    here as a `pathlib.Path`.
    """
    platen = Platen(
        templates_dir.as_posix(),
        output_dir,
        {},
    )

    assert platen.templates_dir == templates_dir
