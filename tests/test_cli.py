import os
from errno import EIO
from importlib.metadata import entry_points
from os import R_OK, W_OK, access, link, strerror
from os.path import exists, isabs, realpath
from pathlib import Path

from jinja2 import TemplateSyntaxError
from pytest import CaptureFixture, MonkeyPatch, fixture, mark, raises, skip

from platen.cli import main
from tests.snapshots import snapshot


def _assert_fails(
    capsys: CaptureFixture[str],
    directory: Path,
    arguments: list[str],
    message: str,
) -> None:
    """
    Assert that running `platen` fails with exit code 1 and a single line of error on
    stderr, prints nothing on stdout, and changes nothing within a directory.

    Only use this for a press that stops on its first template: a press keeps every
    template that it pressed before the error.
    """
    before = snapshot(directory)

    assert main(arguments) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"platen: error: {message}\n"
    assert snapshot(directory) == before


@fixture
def project(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    """
    Create a working directory that holds `README.template`, which includes
    `_badge.txt`, and `values.yaml`, and return its path.
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

    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_console_script_runs_main() -> None:
    """The `platen` console script must run `platen.cli.main`."""
    (script,) = entry_points(group="console_scripts", name="platen")
    assert script.load() is main


def test_help(capsys: CaptureFixture[str], monkeypatch: MonkeyPatch) -> None:
    """`--help` must print usage to stdout and exit with 0."""
    # Python 3.14's argparse colours its output when the environment asks for colour.
    monkeypatch.setenv("PYTHON_COLORS", "0")

    with raises(SystemExit) as ex:
        main(["--help"])

    assert ex.value.code == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("usage: platen [-h] template values output\n")
    assert captured.err == ""


@mark.parametrize(
    "template",
    [
        "templates",
        "templates/",
        "./templates",
        "{cwd}/templates",
        "link",
        "templates/sub/..",
    ],
    ids=[
        "relative",
        "trailing separator",
        "leading dot",
        "absolute",
        "symlink",
        "parent directory",
    ],
)
def test_presses_directory(
    template: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen templates values.yaml build` must press every file within the directory to
    the same path within the output, however the directory is named, print nothing, and
    exit with 0.

    Templates that are included must be found relative to the directory, wherever the
    template that includes them is, and be pressed too.
    """
    templates = project / "templates"
    (templates / "sub").mkdir(parents=True)
    (templates / "_badge.txt").write_text("Badge: {{ status }}\n", encoding="utf-8")

    (templates / "index.md").write_text(
        '# {{ name }}\n\n{% include "_badge.txt" %}\n',
        encoding="utf-8",
    )

    (templates / "sub" / "page.md").write_text(
        '{% include "_badge.txt" %}',
        encoding="utf-8",
    )

    (project / "link").symlink_to("templates")

    assert main([template.format(cwd=project), "values.yaml", "build"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }

    assert pressed == {
        Path("_badge.txt"): b"Badge: passing\n",
        Path("index.md"): b"# platen\n\nBadge: passing\n",
        Path("sub/page.md"): b"Badge: passing\n",
    }


def test_presses_empty_values(capsys: CaptureFixture[str], project: Path) -> None:
    """An empty values file must be read as no values, rather than refused."""
    (project / "doc.template").write_text("Hello, world!\n", encoding="utf-8")
    (project / "empty.yaml").write_text("", encoding="utf-8")

    assert main(["doc.template", "empty.yaml", "doc.md"]) == 0
    assert capsys.readouterr().err == ""
    assert (project / "doc.md").read_text(encoding="utf-8") == "Hello, world!\n"


def test_presses_readme_next_to_template(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen README.template values.yaml README.md` must press the README next to its
    template, resolving its include from the template's directory, print nothing, and
    exit with 0.
    """
    assert main(["README.template", "values.yaml", "README.md"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_presses_symlink_to_template_within_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A template that's a symlink to a file in its own directory must be pressed."""
    (project / "link.template").symlink_to("README.template")

    assert main(["link.template", "values.yaml", "README.md"]) == 0
    assert capsys.readouterr().err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_presses_template_in_subdirectory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template in a subdirectory must resolve its includes from that subdirectory, and
    must be pressed to an output of any name, creating missing directories.
    """
    docs = project / "docs"
    docs.mkdir()
    (docs / "page.template").write_text('{% include "_part.txt" %}', encoding="utf-8")
    (docs / "_part.txt").write_text("Hello from {{ name }}\n", encoding="utf-8")

    assert main(["docs/page.template", "values.yaml", "build/site/index.md"]) == 0
    assert capsys.readouterr().err == ""

    pressed = (project / "build" / "site" / "index.md").read_text(encoding="utf-8")
    assert pressed == "Hello from platen\n"


@mark.parametrize(
    ("ignore", "expect"),
    [
        (
            None,
            {
                Path("index.md"): b"# platen\n",
                Path("values.yaml"): b'name: platen\ngreeting: "platen"\n',
            },
        ),
        (
            "values.yaml\n",
            {
                Path("index.md"): b"# platen\n",
            },
        ),
    ],
    ids=[
        "pressed",
        "ignored",
    ],
)
def test_presses_values_file_within_directory(
    ignore: str | None,
    expect: dict[Path, bytes],
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A values file within the directory must be pressed as a template too, unless a
    `.platenignore` file lists it.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "index.md").write_text("# {{ name }}\n", encoding="utf-8")

    (templates / "values.yaml").write_text(
        'name: platen\ngreeting: "{{ name }}"\n',
        encoding="utf-8",
    )

    if ignore is not None:
        (templates / ".platenignore").write_text(ignore, encoding="utf-8")

    assert main(["templates", "templates/values.yaml", "build"]) == 0
    assert capsys.readouterr().err == ""

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }

    assert pressed == expect


@mark.filterwarnings("error")
def test_presses_values_that_reuse_anchor(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A values file that reuses an anchor must press without printing a warning."""
    (project / "values.yaml").write_text(
        "name: &x a\nstatus: &x passing\n",
        encoding="utf-8",
    )

    assert main(["README.template", "values.yaml", "README.md"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# a\n\nBuild: passing\n"


def test_refuses_directory_destination_that_is_template(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A destination that's the same file as a template within the directory, like
    through a hard link, must be refused, naming the destination as it was given and
    the template absolutely.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "a.md").write_text("A\n", encoding="utf-8")
    (project / "build").mkdir()
    link(templates / "a.md", project / "build" / "a.md")

    _assert_fails(
        capsys,
        project,
        ["templates", "values.yaml", "build"],
        "Destination 'build/a.md' is the same file as template "
        f"'{realpath(templates / 'a.md')}'",
    )


def test_refuses_directory_destination_that_is_values_file(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A destination that's the same file as the values file, like through a symlink
    within the output, must be refused, naming the destination as it was given and the
    values file absolutely.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "a.md").write_text("A\n", encoding="utf-8")
    (project / "build").mkdir()
    (project / "build" / "a.md").symlink_to("../values.yaml")

    _assert_fails(
        capsys,
        project,
        ["templates", "values.yaml", "build"],
        "Destination 'build/a.md' is the same file as protected file "
        f"'{realpath(project / 'values.yaml')}'",
    )


def test_refuses_directory_output_within_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    An output within the directory being pressed must be refused, naming the output as
    it was given and the directory absolutely.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "a.md").write_text("A\n", encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["templates", "values.yaml", "templates/build"],
        f"Destination 'templates/build' is within '{realpath(templates)}', the "
        "directory being pressed",
    )


def test_refuses_directory_template_after_pressing_others(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template within the directory that can't be rendered must be refused, naming it
    absolutely, and the line, and the templates pressed before it must stay pressed.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "a.md").write_text("A\n", encoding="utf-8")
    (templates / "b.md").write_text("{{ missing }}\n", encoding="utf-8")

    # `_assert_fails` would see `a.md`'s result as a change.
    assert main(["templates", "values.yaml", "build"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: error: 'missing' is undefined "
        f"({realpath(templates / 'b.md')}, line 1)\n"
    )

    build = project / "build"
    assert [path.relative_to(build) for path in build.rglob("*")] == [Path("a.md")]
    assert (build / "a.md").read_bytes() == b"A\n"


def test_refuses_directory_that_cannot_be_read(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A directory that can't be listed must be refused, naming it absolutely, before
    anything is pressed.
    """
    directory = project / "templates"
    directory.mkdir()
    (directory / "a.md").write_text("A\n", encoding="utf-8")
    directory.chmod(0o300)

    try:
        if access(directory, R_OK):
            skip("Permissions don't stop this user from reading directories")

        # `_assert_fails` can't snapshot a directory that can't be read.
        assert main(["templates", "values.yaml", "build"]) == 1

        captured = capsys.readouterr()
        assert captured.out == ""

        assert captured.err == (
            f"platen: error: Permission denied: '{realpath(directory)}'\n"
        )

        assert not (project / "build").exists()
    finally:
        directory.chmod(0o755)


@mark.parametrize(
    ("name", "target", "message"),
    [
        ("broken", "missing", "Template 'broken' not found"),
        (
            "null.md",
            "/dev/null",
            "Template '{templates}/null.md' can't be pressed: it isn't a regular file",
        ),
        (
            "pipe",
            "../fifo",
            "Template '{templates}/pipe' can't be pressed: it isn't a regular file",
        ),
        (
            "linked",
            "sub",
            "Template '{templates}/linked' can't be pressed: it isn't a regular file",
        ),
    ],
    ids=[
        "broken",
        "to device",
        "to fifo",
        "to directory",
    ],
)
def test_refuses_directory_with_symlink_that_cannot_be_pressed(
    name: str,
    target: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A symlink within the directory that can't be pressed must be refused: naming it
    within the directory when it's broken, or absolutely when it leads to something
    that isn't a regular file, like a directory, a device or a FIFO.
    """
    if target == "../fifo":
        # Not every platform has FIFOs, like Windows.
        if not hasattr(os, "mkfifo"):
            skip("This platform doesn't have FIFOs")

        os.mkfifo(project / "fifo")
    elif isabs(target) and not exists(target):
        skip(f"This platform doesn't have {target}")

    templates = project / "templates"
    (templates / "sub").mkdir(parents=True)
    (templates / "sub" / "a.md").write_text("A\n", encoding="utf-8")

    # Every symlink sorts before `sub/a.md`, so it's the first template pressed.
    (templates / name).symlink_to(target)

    _assert_fails(
        capsys,
        project,
        ["templates", "values.yaml", "build"],
        message.format(templates=realpath(templates)),
    )


def test_refuses_output_in_directory_that_cannot_be_written(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    An output in a directory that can't be written must be refused, naming the output
    as it was given.
    """
    directory = project / "ro"
    directory.mkdir()
    directory.chmod(0o555)

    try:
        if access(directory, W_OK):
            skip("Permissions don't stop this user from writing files")

        _assert_fails(
            capsys,
            project,
            ["README.template", "values.yaml", "ro/out.md"],
            "Permission denied: 'ro/out.md'",
        )
    finally:
        directory.chmod(0o755)


def test_refuses_output_that_cannot_be_written(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """An output that can't be written must be refused, naming it as it was given."""
    (project / "existing").mkdir()

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "existing"],
        "Is a directory: 'existing'",
    )


@mark.parametrize(
    ("error", "message"),
    [
        (OSError(EIO, strerror(EIO)), strerror(EIO)),
        (OSError(EIO, strerror(EIO), "out.md"), f"{strerror(EIO)}: 'out.md'"),
        (
            OSError(EIO, strerror(EIO), "README.template", None, "out.md"),
            f"{strerror(EIO)}: 'README.template' -> 'out.md'",
        ),
        (OSError("Disk on fire"), "OSError: Disk on fire"),
    ],
    ids=[
        "names no paths",
        "names one path",
        "names two paths",
        "not described",
    ],
)
def test_refuses_output_that_fails_to_write(
    error: OSError,
    message: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    An output that fails while it's being written must be refused, naming every path
    that the error names as the error names it, or naming the error's type when the
    operating system doesn't describe it.
    """

    def press_file(*_: object) -> None:
        raise error

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file)

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "out.md"],
        message,
    )


def test_refuses_output_that_is_template(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    An output that's the same file as the template must be refused, naming the output
    as it was given and the template absolutely.
    """
    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "README.template"],
        "Destination 'README.template' is the same file as template "
        f"'{realpath(project / 'README.template')}'",
    )


def test_refuses_output_that_is_values_file(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    The output must be refused when it's the same file as the values file, naming the
    output as it was given and the values file absolutely, and the values file must be
    left as it was.
    """
    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "values.yaml"],
        "Destination 'values.yaml' is the same file as protected file "
        f"'{realpath(project / 'values.yaml')}'",
    )


def test_refuses_paths_in_deleted_working_directory(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    Paths relative to a working directory that's been deleted must be refused, naming
    them as they were given.
    """
    working_dir = project / "gone"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    working_dir.rmdir()

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "README.md"],
        "No such file or directory: 'README.template'",
    )


def test_refuses_template_error_without_file(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    A template error that Jinja doesn't name a file for must be refused without a
    location, rather than a guessed one.
    """

    def press_file(*_: object) -> None:
        error = TemplateSyntaxError("Broken", 2)
        code = compile("raise error", "<unknown>", "exec")
        exec(code, {"__jinja_exception__": error, "error": error})

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file)
    _assert_fails(
        capsys, project, ["README.template", "values.yaml", "out.md"], "Broken"
    )


@mark.parametrize(
    ("template", "message"),
    [
        (
            "missing.template",
            "No such file or directory: 'missing.template'",
        ),
        (
            "README.template/",
            "Not a directory: 'README.template/'",
        ),
    ],
    ids=[
        "missing",
        "trailing separator",
    ],
)
def test_refuses_template_that_cannot_be_found(
    template: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template that isn't there, as it was given, must be refused, naming it as it was
    given, rather than tidied up into a template that is there.
    """
    _assert_fails(
        capsys,
        project,
        [template, "values.yaml", "out.md"],
        message,
    )


def test_refuses_template_that_cannot_be_read(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A template that can't be read must be refused, naming it absolutely."""
    template = project / "README.template"
    template.chmod(0)

    try:
        if access(template, R_OK):
            skip("Permissions don't stop this user from reading files")

        # `_assert_fails` can't snapshot a file that can't be read.
        assert main(["README.template", "values.yaml", "README.md"]) == 1

        captured = capsys.readouterr()
        assert captured.out == ""

        assert captured.err == (
            f"platen: error: Permission denied: '{realpath(template)}'\n"
        )

        assert not (project / "README.md").exists()
    finally:
        template.chmod(0o644)


@mark.parametrize(
    ("files", "message"),
    [
        (
            {"page.template": "Hello\n{{ missing }}\n"},
            "'missing' is undefined ({docs}/page.template, line 2)",
        ),
        (
            {
                "page.template": 'Hello\n\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n{{ missing.attribute }}\n",
            },
            "'missing' is undefined ({docs}/_part.txt, line 2)",
        ),
        (
            {"page.template": "Hello\n{% if %}\n"},
            "Expected an expression, got 'end of statement block' "
            "({docs}/page.template, line 2)",
        ),
        (
            {
                "page.template": 'Hello\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n\n{% endif %}\n",
            },
            "Encountered unknown tag 'endif'. ({docs}/_part.txt, line 3)",
        ),
        (
            {
                "page.template": 'Hello\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n{# comment\n",
            },
            "Missing end of comment tag ({docs}/_part.txt, line 2)",
        ),
        (
            {"page.template": 'Hello\n{% include "_missing.txt" %}\n'},
            "Template '_missing.txt' not found ({docs}/page.template, line 2)",
        ),
        (
            {"page.template": 'Hello\n{% include ["_a.txt", "_b.txt"] %}\n'},
            "none of the templates given were found: _a.txt, _b.txt "
            "({docs}/page.template, line 2)",
        ),
        (
            {"page.template": "Hello\n{{ 1 / 0 }}\n"},
            "ZeroDivisionError: division by zero ({docs}/page.template, line 2)",
        ),
    ],
    ids=[
        "undefined",
        "undefined in include",
        "syntax error",
        "syntax error in include",
        "unclosed comment in include",
        "missing include",
        "missing includes",
        "python error",
    ],
)
def test_refuses_template_that_cannot_be_rendered(
    files: dict[str, str],
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template that can't be rendered must be refused, naming the template that failed
    absolutely, as Jinja names it, and the line.
    """
    docs = project / "docs"
    docs.mkdir()

    for name, body in files.items():
        (docs / name).write_text(body, encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["docs/page.template", "values.yaml", "out.md"],
        message.format(docs=realpath(docs)),
    )


def test_refuses_template_that_is_not_a_file_or_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template that's neither a regular file nor a directory, like a FIFO, must be
    refused, naming it absolutely.
    """
    # Not every platform has FIFOs, like Windows.
    if not hasattr(os, "mkfifo"):
        skip("This platform doesn't have FIFOs")

    os.mkfifo(project / "fifo")

    _assert_fails(
        capsys,
        project,
        ["fifo", "values.yaml", "out.md"],
        f"Template '{realpath(project / 'fifo')}' can't be pressed: it isn't a "
        "regular file",
    )


def test_refuses_template_that_is_not_utf8(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A template that isn't UTF-8 must be refused, naming it absolutely, and why."""
    (project / "docs").mkdir()
    (project / "docs" / "_legacy.txt").write_bytes(b"Caf\xe9\n")

    _assert_fails(
        capsys,
        project,
        ["docs/_legacy.txt", "values.yaml", "out.md"],
        f"Template '{realpath(project / 'docs' / '_legacy.txt')}' can't be pressed: "
        "it isn't valid UTF-8 (invalid continuation byte at byte 3)",
    )


def test_refuses_template_that_leads_outside_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template that's a symlink to a file outside its own directory must be refused,
    naming where it leads and its directory absolutely, because its includes are
    resolved from the directory of the symlink.
    """
    (project / "docs").mkdir()
    (project / "docs" / "link.template").symlink_to(project / "README.template")

    _assert_fails(
        capsys,
        project,
        ["docs/link.template", "values.yaml", "out.md"],
        f"Template '{realpath(project / 'README.template')}' is not within the "
        f"templates directory '{realpath(project / 'docs')}'",
    )


@mark.parametrize(
    ("body", "message"),
    [
        (
            "- platen\n",
            'Values file "it\'s\\nvalues.yaml" must hold a mapping of names to values',
        ),
        (
            "name: platen: x\n",
            "mapping values are not allowed here "
            "(it's\\nvalues.yaml, line 1, column 13)",
        ),
    ],
    ids=[
        "quoted",
        "unquoted",
    ],
)
def test_refuses_values_named_with_line_break(
    body: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A values file named with a line break must be refused on one line, escaping its
    name like other paths, whether or not it's quoted.
    """
    (project / "it's\nvalues.yaml").write_text(body, encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["README.template", "it's\nvalues.yaml", "README.md"],
        message,
    )


@mark.parametrize(
    "body",
    [
        "- platen\n",
        "platen\n",
        "42\n",
        "2026-10-08\n",
    ],
    ids=[
        "list",
        "string",
        "number",
        "date",
    ],
)
def test_refuses_values_that_are_not_mapping(
    body: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """Values that aren't a mapping must be refused."""
    (project / "values.yaml").write_text(body, encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "README.md"],
        "Values file 'values.yaml' must hold a mapping of names to values",
    )


@mark.parametrize(
    ("body", "message"),
    [
        (
            b"name: platen: x\n",
            "mapping values are not allowed here (values.yaml, line 1, column 13)",
        ),
        (
            b"name: platen\nstatus: passing\nname: other\n",
            'while constructing a mapping, found duplicate key "name" with value '
            '"other" (original value: "platen") (values.yaml, line 3, column 1)',
        ),
        (
            b"name: platen\n---\nname: other\n",
            "expected a single document in the stream, but found another document "
            "(values.yaml, line 2, column 1)",
        ),
        (
            b"name:\n\tstatus: passing\n",
            "while scanning for the next token, found character '\\t' that cannot "
            "start any token (values.yaml, line 2, column 1)",
        ),
        (
            b"name: \xff\n",
            "unacceptable character #x00ff: invalid start byte "
            'in "values.yaml", position 6',
        ),
        (
            b"%YAML 1.3\n---\nname: platen\n",
            "version minor part can only be 2 or 1, got (1, 3) (values.yaml)",
        ),
    ],
    ids=[
        "syntax error",
        "duplicate key",
        "multiple documents",
        "tab",
        "not utf-8",
        "unsupported version",
    ],
)
def test_refuses_values_that_are_not_valid(
    body: bytes,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """Values that aren't valid YAML must be refused on one line, saying where."""
    (project / "values.yaml").write_bytes(body)

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "README.md"],
        message,
    )


@mark.parametrize(
    ("values", "message"),
    [
        ("missing.yaml", "No such file or directory: 'missing.yaml'"),
        ("{cwd}/missing.yaml", "No such file or directory: '{cwd}/missing.yaml'"),
        ("docs", "Is a directory: 'docs'"),
    ],
    ids=[
        "missing",
        "missing absolute",
        "directory",
    ],
)
def test_refuses_values_that_cannot_be_read(
    values: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A values file that can't be read must be refused, naming it exactly as it was
    given, even when it's within the template's directory.
    """
    (project / "docs").mkdir()

    _assert_fails(
        capsys,
        project,
        ["README.template", values.format(cwd=project), "README.md"],
        message.format(cwd=project),
    )


@mark.parametrize(
    ("body", "key"),
    [
        ("1: one\n", "1"),
        ("true: one\n", "True"),
    ],
    ids=[
        "number",
        "boolean",
    ],
)
def test_refuses_values_with_keys_that_are_not_strings(
    body: str,
    key: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """Values with a key that isn't a string must be refused, naming the key."""
    (project / "values.yaml").write_text(body, encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", "README.md"],
        f"Values file 'values.yaml' has a key that isn't a string: {key}",
    )


@mark.parametrize(
    "arguments",
    [
        [],
        ["README.template"],
        ["README.template", "values.yaml"],
        ["README.template", "values.yaml", "README.md", "extra"],
    ],
    ids=[
        "none",
        "one",
        "two",
        "four",
    ],
)
def test_refuses_wrong_number_of_arguments(
    arguments: list[str],
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    The wrong number of arguments must print usage and an error to stderr, exit with 2,
    and write nothing.
    """
    # Python 3.14's argparse colours its output when the environment asks for colour.
    monkeypatch.setenv("PYTHON_COLORS", "0")
    before = snapshot(project)

    with raises(SystemExit) as ex:
        main(arguments)

    assert ex.value.code == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: platen [-h] template values output\n")
    assert "\nplaten: error: " in captured.err
    assert snapshot(project) == before


@mark.parametrize(
    ("template", "method"),
    [
        (".", "press"),
        ("README.template", "press_file"),
    ],
    ids=[
        "directory",
        "file",
    ],
)
def test_stops_quietly_when_interrupted(
    template: str,
    method: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """An interrupt must stop `platen` quietly, with exit code 130."""

    def interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(f"platen.cli.Platen.{method}", interrupt)

    assert main([template, "values.yaml", "README.md"]) == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@mark.parametrize(
    ("template", "files"),
    [
        ("docs", {}),
        ("docs/", {}),
        ("docs", {".platenignore": "*.md\n", "page.md": "Hello\n"}),
    ],
    ids=[
        "empty",
        "trailing separator",
        "everything ignored",
    ],
)
def test_warns_when_directory_has_nothing_to_press(
    template: str,
    files: dict[str, str],
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A directory with nothing to press must print a warning that names it as it was
    given, write nothing, and exit with 0.
    """
    (project / "docs").mkdir()

    for name, body in files.items():
        (project / "docs" / name).write_text(body, encoding="utf-8")

    before = snapshot(project)

    assert main([template, "values.yaml", "build"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: warning: Nothing to press in '{template}': it's empty, or "
        "everything in it is ignored\n"
    )

    assert snapshot(project) == before
