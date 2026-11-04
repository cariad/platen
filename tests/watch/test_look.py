from errno import EIO
from os import strerror
from os.path import join, realpath
from pathlib import Path

from pytest import MonkeyPatch, fixture, mark

from platen import Platen
from platen.watch import Look, look, status
from tests.edits import edit


@fixture
def project(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    """
    Create a working directory that holds `README.template`, which includes
    `_badge.txt`, `values.yaml` and `templates`, a directory of templates, and return
    its path.

    `templates` holds `doc.md`, which includes `_footer.md`, and `sub/doc.md`. A walk
    finds neither `_footer.md`, which `.platenignore` ignores, nor `logo.png`, which has
    a binary file's name. `linked` is a symlink to `templates`.
    """
    (tmp_path / "README.template").write_text(
        '# {{ name }}\n\n{% include "_badge.txt" %}\n',
        encoding="utf-8",
    )

    (tmp_path / "_badge.txt").write_text("Build: {{ status }}\n", encoding="utf-8")

    (tmp_path / "values.yaml").write_text(
        "name: platen\nstatus: passing\n",
        encoding="utf-8",
    )

    templates_dir = tmp_path / "templates"
    (templates_dir / "sub").mkdir(parents=True)
    (templates_dir / ".platenignore").write_text("_*\n", encoding="utf-8")
    (templates_dir / "_footer.md").write_text("Footer\n", encoding="utf-8")

    (templates_dir / "doc.md").write_text(
        '{% include "_footer.md" %}\n',
        encoding="utf-8",
    )

    (templates_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (templates_dir / "sub" / "doc.md").write_text("Hello\n", encoding="utf-8")
    (tmp_path / "linked").symlink_to("templates", target_is_directory=True)

    monkeypatch.chdir(tmp_path)
    return tmp_path


@mark.parametrize(
    ("template", "read", "edited"),
    [
        ("README.template", "_badge.txt", "README.template"),
        ("README.template", "_badge.txt", "values.yaml"),
        # A template that the last press read, because `README.template` includes it.
        ("README.template", "_badge.txt", "_badge.txt"),
        # A template that the walk ignores, but that the last press read, because
        # `doc.md` includes it.
        ("templates", "templates/_footer.md", "templates/_footer.md"),
        # A template that only the walk finds, because the last press read only
        # `_footer.md`.
        ("templates", "templates/_footer.md", "templates/sub/doc.md"),
    ],
)
def test_look_changes_when_file_is_edited(
    template: str,
    read: str,
    edited: str,
    project: Path,
) -> None:
    """
    `look` must see an edit to anything that a press depends on, even an edit that
    keeps the file's size.
    """
    read_paths = [realpath(project / read)]
    before = look(template, "values.yaml", read_paths)

    # Upper-casing the file keeps its size, so only its times tell the edit apart.
    path = project / edited
    edit(path, path.read_text(encoding="utf-8").upper())

    result = look(template, "values.yaml", read_paths)

    assert result != before


def test_look_changes_when_template_that_was_read_is_ignored(project: Path) -> None:
    """
    `look` must see a template that the last press read become ignored, by the names
    that the walk finds, even though its path is still looked at and no status has
    changed.
    """
    templates_dir = realpath(project / "templates")
    read = [
        join(templates_dir, "_footer.md"),
        join(templates_dir, "doc.md"),
        join(templates_dir, "sub/doc.md"),
    ]

    before = look("templates", "values.yaml", read)

    edit(project / "templates" / ".platenignore", "_*\n/doc.md\n")
    after = look("templates", "values.yaml", read)

    assert before.names == ("doc.md", "sub/doc.md")
    assert after.names == ("sub/doc.md",)
    assert after.statuses == before.statuses


def test_look_does_not_change_when_ignore_file_gains_comment(project: Path) -> None:
    """
    `look` must not see an edit to a `.platenignore` file that doesn't change what a
    walk finds, like a new comment, because a press would press the same templates.
    """
    read = [realpath(project / "templates" / "_footer.md")]
    before = look("templates", "values.yaml", read)

    edit(
        project / "templates" / ".platenignore",
        "# Partials are only ever included.\n_*\n",
    )

    result = look("templates", "values.yaml", read)

    assert result == before


def test_look_finds_template_values_and_templates_read(project: Path) -> None:
    """
    `look` must find the status of the template, the values file and each template
    that the last press read, by their paths as given, and no names, when the template
    is a file. Files beside the template that the press didn't read are left out.
    """
    badge = realpath(project / "_badge.txt")

    found = look("README.template", "values.yaml", [badge])
    expect = Look(
        None,
        {
            "README.template": status("README.template"),
            "values.yaml": status("values.yaml"),
            badge: status(badge),
        },
    )

    assert found == expect


@mark.parametrize(
    "template",
    [
        "templates",
        # A symlink to `templates`, so the walked templates are looked at within the
        # directory that it resolves to.
        "linked",
    ],
)
def test_look_finds_walked_templates_in_directory(
    template: str,
    project: Path,
) -> None:
    """
    `look` must find the names of the templates that a walk of a templates directory
    finds, in walk order, and the status of each by its path within the resolved
    templates directory, as well as the status of the template, the values file and
    each template that the last press read.

    A walk finds what a press would press, so it leaves out ignored files,
    `.platenignore` files and binary files' names.
    """
    templates_dir = realpath(project / "templates")
    footer = join(templates_dir, "_footer.md")

    found = look(template, "values.yaml", [footer])

    paths = [
        template,
        "values.yaml",
        footer,
        join(templates_dir, "doc.md"),
        join(templates_dir, "sub/doc.md"),
    ]
    expect = Look(("doc.md", "sub/doc.md"), {p: status(p) for p in paths})

    assert found == expect


def test_look_watches_file_that_stops_walk(project: Path) -> None:
    """
    `look` must find no names when a walk of the templates directory fails, and look at
    the file that the walk failed on, like a broken `.platenignore` symlink, so that
    fixing it is a change.
    """
    ignore_file = project / "templates" / ".platenignore"
    ignore_file.unlink()
    ignore_file.symlink_to("missing")

    found = look("templates", "values.yaml", [])
    expect = Look(
        None,
        {
            "templates": status("templates"),
            "values.yaml": status("values.yaml"),
            join(realpath(project / "templates"), ".platenignore"): None,
        },
    )

    assert found == expect


def test_look_watches_missing_template(project: Path) -> None:
    """
    `look` must find no status for a template that's missing, and no names, so that
    the template appearing is a change.
    """
    found = look("missing.md", "values.yaml", [])
    expect = Look(
        None,
        {
            "missing.md": None,
            "values.yaml": status("values.yaml"),
        },
    )

    assert found == expect


def test_look_watches_templates_directory_when_walk_error_names_no_file(
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `look` must look at the templates directory itself when a walk fails with an error
    that names no file, rather than at a path named "None".
    """

    def templates(*_: object) -> list[str]:
        raise OSError(EIO, strerror(EIO))

    # Patch the class, because `platen.watch.look` names the function, which shadows
    # its module.
    monkeypatch.setattr(Platen, "templates", templates)

    found = look("templates", "values.yaml", [])
    expect = Look(
        None,
        {
            "templates": status("templates"),
            "values.yaml": status("values.yaml"),
        },
    )

    assert found == expect
