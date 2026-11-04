from errno import EACCES, EIO
from importlib import import_module
from os import link, replace, strerror, symlink
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


def test_look_changes_when_template_symlink_is_retargeted_to_hard_link(
    project: Path,
) -> None:
    """
    `look` must see a symlink to the template retargeted to a hard link to the same
    file by another name, by the template's name, even though no status has changed,
    because Jinja chooses autoescaping by that name.
    """
    (project / "body.txt").write_text("<p>{{ name }}</p>\n", encoding="utf-8")
    link(project / "body.txt", project / "body.html")
    symlink("body.txt", project / "page.template")

    before = look("page.template", "values.yaml", [])

    # Retarget the symlink atomically, like `ln -sfn` does.
    symlink("body.html", project / "page.new")
    replace(project / "page.new", project / "page.template")

    after = look("page.template", "values.yaml", [])

    assert before.names == ("body.txt",)
    assert after.names == ("body.html",)
    assert after.statuses == before.statuses


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


def test_look_changes_when_templates_directory_symlink_is_retargeted_to_hard_links(
    project: Path,
) -> None:
    """
    `look` must see a symlink to the template's directory retargeted to a copy made of
    hard links, by the templates directory, even though no status has changed, because
    includes are found there.
    """
    for directory in ("a", "b"):
        (project / directory).mkdir()

    (project / "a" / "page.md").write_text(
        '{% include "_footer.md" %}\n',
        encoding="utf-8",
    )

    (project / "a" / "_footer.md").write_text("Footer\n", encoding="utf-8")

    # A copy made of hard links, like `cp -al` makes.
    for name in ("page.md", "_footer.md"):
        link(project / "a" / name, project / "b" / name)

    symlink("a", project / "site", target_is_directory=True)
    read = [realpath(project / "a" / "_footer.md")]

    before = look("site/page.md", "values.yaml", read)

    # Retarget the symlink atomically, like `ln -sfn` does.
    symlink("b", project / "site.new", target_is_directory=True)
    replace(project / "site.new", project / "site")

    after = look("site/page.md", "values.yaml", read)

    assert before.templates_dir == realpath(project / "a")
    assert after.templates_dir == realpath(project / "b")
    assert after.names == before.names
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


def test_look_finds_no_name_when_template_cannot_be_named(
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `look` must find no templates directory and no name when the template can't be
    named, like when a part of its path can't be read, because a press would fail too.
    """

    def relative_within(*_: object) -> None:
        raise PermissionError(EACCES, strerror(EACCES))

    # Import the module, because `platen.watch.look` names the function, which shadows
    # its module.
    monkeypatch.setattr(
        import_module("platen.watch.look"),
        "relative_within",
        relative_within,
    )

    found = look("README.template", "values.yaml", [])
    expect = Look(
        None,
        None,
        {
            "README.template": status("README.template"),
            "values.yaml": status("values.yaml"),
        },
    )

    assert found == expect


def test_look_finds_no_name_when_template_leads_outside_its_directory(
    project: Path,
) -> None:
    """
    `look` must find no name for a template that leads outside its own directory,
    through a symlink, because a press would refuse it.
    """
    (project / "sub").mkdir()
    symlink("../README.template", project / "sub" / "page.template")

    found = look("sub/page.template", "values.yaml", [])
    expect = Look(
        realpath(project / "sub"),
        None,
        {
            "sub/page.template": status("sub/page.template"),
            "values.yaml": status("values.yaml"),
        },
    )

    assert found == expect


def test_look_finds_template_values_and_templates_read(project: Path) -> None:
    """
    `look` must find the status of the template, the values file and each template
    that the last press read, by their paths as given, the template's directory as the
    templates directory, and the template's name, when the template is a file. Files
    beside the template that the press didn't read are left out.
    """
    badge = realpath(project / "_badge.txt")

    found = look("README.template", "values.yaml", [badge])
    expect = Look(
        realpath(project),
        ("README.template",),
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
    expect = Look(
        templates_dir,
        ("doc.md", "sub/doc.md"),
        {p: status(p) for p in paths},
    )

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
        realpath(project / "templates"),
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
        realpath(project / "templates"),
        None,
        {
            "templates": status("templates"),
            "values.yaml": status("values.yaml"),
        },
    )

    assert found == expect
