from os import link
from os.path import exists, realpath
from pathlib import Path
from re import escape

from pytest import MonkeyPatch, mark, raises, skip
from rootle.testing import (
    can_chmod_deny_writing,
    snapshot,
)

from platen import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    Platen,
)


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
    result = (tmp_path / "doc.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


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

    result = (project_dir / "file.md").read_text(encoding="utf-8")

    assert result == "Hello\n"

    result = (project_dir / "sub" / "file.md").read_text(encoding="utf-8")

    assert result == "Hello\n"

    result = (templates_dir / "file.md").read_text(encoding="utf-8")

    assert result == "{{ greeting }}\n"


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

    result = values.read_text(encoding="utf-8")

    assert result == "greeting: Hello\n"


def test_press_raises_for_destination_that_cannot_be_written(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `press` must name a destination that can't be written as it was given, even when a
    symlink within the destination leads elsewhere, like every other error about it.
    """
    if not can_chmod_deny_writing(tmp_path):
        skip("Permissions don't stop this user from writing files")

    templates_dir = tmp_path / "source"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / "sub" / "page.md").write_text("Page\n", encoding="utf-8")
    directory = tmp_path / "ro"
    directory.mkdir()
    output_dir.mkdir()
    (output_dir / "sub").symlink_to(directory)
    directory.chmod(0o555)

    try:
        platen = Platen(
            templates_dir,
            {},
        )

        with raises(PermissionError) as ex:
            platen.press(output_dir)

        expect = str(output_dir / "sub" / "page.md")

        assert ex.value.filename == expect
    finally:
        directory.chmod(0o755)


@mark.parametrize(
    ("method", "destination", "expect_destination"),
    [
        ("press_file", "values.yaml", "values.yaml"),
        # A symlink to the protected file.
        ("press_file", "link.yaml", "link.yaml"),
        # A hard link to the protected file.
        ("press_file", "hard.yaml", "hard.yaml"),
        # Steps up out of a directory that doesn't exist yet, so the path only leads to
        # the protected file once the press creates that directory.
        ("press_file", "missing/../values.yaml", "missing/../values.yaml"),
        # The walk would press `doc.md` to a hard link to the protected file.
        ("press_directory", ".", "doc.md"),
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

    expect_path = tmp_path / expect_destination

    assert ex.value.destination_path == expect_path

    expect_path = tmp_path / "values.yaml"

    assert ex.value.protected_path == expect_path

    result = snapshot(tmp_path)

    assert result == before


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
        # Steps up out of a directory that doesn't exist yet, so the path only leads to
        # the template once the press creates that directory.
        (
            "press_file",
            "doc.md",
            "missing/../templates/doc.md",
            "doc.md",
            "missing/../templates/doc.md",
        ),
        # A symlink to the template, from outside the templates directory.
        ("press_file", "doc.md", "doc_link.md", "doc.md", "doc_link.md"),
        # A hard link to the template, from outside the templates directory.
        ("press_file", "doc.md", "doc_hard.md", "doc.md", "doc_hard.md"),
        # `posts/a.md` would be pressed to a hard link to `shared/b.md`, which
        # `posts/link.md` leads to, so the destination is a template yet to be pressed.
        ("press_directory", "posts", "out", "posts/link.md", "out/a.md"),
        ("press_file", "doc.md", "templates/DOC.md", "doc.md", "templates/DOC.md"),
        # The destination spells `é` as an `e` and a combining accent, where the
        # template's name has the single, precomposed character.
        (
            "press_file",
            "café.md",
            "templates/café.md",
            "café.md",
            "templates/café.md",
        ),
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

    expect_path = tmp_path / expect_destination

    assert ex.value.destination_path == expect_path

    expect_path = platen.templates_dir / expect_template

    assert ex.value.template_path == expect_path

    result = snapshot(tmp_path)

    assert result == before


@mark.parametrize(
    ("directory", "destination", "expect_directory"),
    [
        (None, "templates", "templates"),
        # Ignored by `.platenignore`, which doesn't stop it being within.
        (None, "templates/build", "templates"),
        ("posts", "templates/posts/build", "templates/posts"),
        # An existing file within, which must be refused for being within rather than
        # for being in the way.
        (None, "templates/a.md", "templates"),
        # Within the templates directory through a symlink to it.
        (None, "alias/build", "templates"),
        (None, "TEMPLATES/build", "templates"),
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

    expect_path = Path(destination)

    assert ex.value.destination_path == expect_path

    expect_path = tmp_path / expect_directory

    assert ex.value.directory == expect_path

    result = snapshot(tmp_path)

    assert result == before
