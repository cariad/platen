import os
from errno import EIO
from importlib.metadata import entry_points
from os import R_OK, W_OK, X_OK, access, link, strerror
from os.path import exists, realpath
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


@mark.parametrize(
    ("output", "named"),
    [
        ("link/out.md", "link/out.md"),
        ("ro/../ro/out.md", "ro/../ro/out.md"),
        ("link/sub/out.md", "link/sub"),
        ("ro/sub/out.md", "ro/sub"),
    ],
    ids=[
        "through symlink",
        "through parent",
        "new directory through symlink",
        "new directory",
    ],
)
def test_refuses_output_in_directory_that_cannot_be_written(
    output: str,
    named: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    An output in a directory that can't be written must be refused, naming the output,
    or the directory that can't be created, as it was given, even though Platen writes
    into the directory's resolved path.
    """
    directory = project / "ro"
    directory.mkdir()
    (project / "link").symlink_to("ro")
    directory.chmod(0o555)

    try:
        if access(directory, W_OK):
            skip("Permissions don't stop this user from writing files")

        _assert_fails(
            capsys,
            project,
            ["README.template", "values.yaml", output],
            f"Permission denied: '{named}'",
        )
    finally:
        directory.chmod(0o755)


def test_refuses_output_in_directory_that_cannot_be_written_elsewhere(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    An output that a symlink leads into a directory that can't be created must be
    refused, naming that directory absolutely, because it wasn't given at all.
    """
    (project / "docs").mkdir()
    (project / "docs" / "page.template").write_text("Hello\n", encoding="utf-8")
    directory = project / "ro"
    directory.mkdir()
    (project / "deep").symlink_to("ro/missing/sub")
    directory.chmod(0o555)

    try:
        if access(directory, W_OK):
            skip("Permissions don't stop this user from writing files")

        _assert_fails(
            capsys,
            project,
            ["docs/page.template", "values.yaml", "deep/out.md"],
            f"Permission denied: '{realpath(directory / 'missing')}'",
        )
    finally:
        directory.chmod(0o755)


@mark.parametrize(
    ("output", "message"),
    [
        ("build/", "Is a directory: 'build/'"),
        ("existing", "Is a directory: 'existing'"),
        ("README.template/out.md", "Not a directory: 'README.template/out.md'"),
        ("loop", "Too many levels of symbolic links: 'loop'"),
        ("values.yaml/", "Is a directory: 'values.yaml/'"),
        (
            "README.template/../values.yaml",
            "Not a directory: 'README.template/../values.yaml'",
        ),
        (
            "missing/../values.yaml/",
            "Is a directory: 'missing/../values.yaml/'",
        ),
    ],
    ids=[
        "names directory",
        "existing directory",
        "within file",
        "looping symlink",
        "names directory at values file",
        "steps through file to values file",
        "names directory at values file through missing directory",
    ],
)
def test_refuses_output_that_cannot_be_written(
    output: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """An output that can't be written must be refused, naming it as it was given."""
    (project / "existing").mkdir()
    (project / "loop").symlink_to("loop")
    _assert_fails(capsys, project, ["README.template", "values.yaml", output], message)


@mark.parametrize(
    ("template", "message"),
    [
        ("README.template", strerror(EIO)),
        ("logo.bin", f"{strerror(EIO)}: 'logo.bin' -> 'out.md'"),
    ],
    ids=[
        "names no paths",
        "names two paths",
    ],
)
def test_refuses_output_that_fails_to_write(
    template: str,
    message: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    An output that fails while it's being written must be refused, naming every path
    that the error names as it was given.
    """
    (project / "logo.bin").write_bytes(b"\0logo")

    def copyfile(source: Path, destination: Path) -> None:
        raise OSError(EIO, strerror(EIO), str(source), None, str(destination))

    def write_text(*_: object, **__: object) -> None:
        raise OSError(EIO, strerror(EIO))

    monkeypatch.setattr("platen.platen.copyfile", copyfile)
    monkeypatch.setattr(Path, "write_text", write_text)

    _assert_fails(capsys, project, [template, "values.yaml", "out.md"], message)


def test_refuses_output_that_is_absolute_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """An absolute output must be named as it was given when it's refused."""
    output = str(project / "existing")
    (project / "existing").mkdir()

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", output],
        f"Is a directory: '{output}'",
    )


@mark.parametrize(
    "output",
    [
        "README.template",
        "link.md",
        "readme.TEMPLATE",
    ],
    ids=[
        "same path",
        "symlink",
        "different case",
    ],
)
def test_refuses_output_that_is_template(
    output: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """An output that's the same file as the template, by any name, must be refused."""
    (project / "link.md").symlink_to("README.template")

    # A different case only names the same file on case-insensitive file systems, like
    # macOS's default APFS.
    if not exists(realpath(project / output)):
        skip(f"'{output}' names a different file on this file system")

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", output],
        f"Destination '{output}' is the same file as template 'README.template'",
    )


@mark.parametrize(
    "output",
    [
        "values.yaml",
        "link.yaml",
        "hard.yaml",
        "VALUES.yaml",
        "missing/../values.yaml",
    ],
    ids=[
        "same path",
        "symlink",
        "hard link",
        "different case",
        "through missing directory",
    ],
)
def test_refuses_output_that_is_values_file(
    output: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    The output must be refused when it's the same file as the values file, by any
    name, and the values file must be left as it was.
    """
    (project / "link.yaml").symlink_to("values.yaml")
    link(project / "values.yaml", project / "hard.yaml")

    # A different case only names the same file on case-insensitive file systems, like
    # macOS's default APFS.
    if not exists(realpath(project / output)):
        skip(f"'{output}' names a different file on this file system")

    _assert_fails(
        capsys,
        project,
        ["README.template", "values.yaml", output],
        f"Destination '{output}' is the same file as values file 'values.yaml'",
    )


def test_refuses_output_that_is_values_file_through_unsearchable_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    The output must be refused when it's the values file through a directory that
    can't be searched, because Platen writes where the output resolves to.
    """
    directory = project / "nox"
    directory.mkdir()
    directory.chmod(0o600)

    try:
        if access(directory, X_OK):
            skip("Permissions don't stop this user from searching directories")

        _assert_fails(
            capsys,
            project,
            ["README.template", "values.yaml", "nox/../values.yaml"],
            "Destination 'nox/../values.yaml' is the same file as values file "
            "'values.yaml'",
        )
    finally:
        directory.chmod(0o755)


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
    ("name", "message"),
    [
        ("README.template", "Permission denied: 'README.template'"),
        ("_badge.txt", "Permission denied: '_badge.txt' (README.template, line 3)"),
    ],
    ids=[
        "template",
        "include",
    ],
)
def test_refuses_template_that_cannot_be_read(
    name: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template, or a template that it includes, that can't be read must be refused,
    naming it relative to the template's directory as it was given.
    """
    template = project / name
    template.chmod(0)

    try:
        if access(template, R_OK):
            skip("Permissions don't stop this user from reading files")

        # `_assert_fails` can't snapshot a file that can't be read.
        assert main(["README.template", "values.yaml", "README.md"]) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"platen: error: {message}\n"
        assert not (project / "README.md").exists()
    finally:
        template.chmod(0o644)


@mark.parametrize(
    ("files", "message"),
    [
        (
            {"page.template": "Hello\n{{ missing }}\n"},
            "'missing' is undefined (docs/page.template, line 2)",
        ),
        (
            {
                "page.template": 'Hello\n\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n{{ missing.attribute }}\n",
            },
            "'missing' is undefined (docs/_part.txt, line 2)",
        ),
        (
            {"page.template": "Hello\n{% if %}\n"},
            "Expected an expression, got 'end of statement block' "
            "(docs/page.template, line 2)",
        ),
        (
            {
                "page.template": 'Hello\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n\n{% endif %}\n",
            },
            "Encountered unknown tag 'endif'. (docs/_part.txt, line 3)",
        ),
        (
            {
                "page.template": 'Hello\n{% include "_part.txt" %}\n',
                "_part.txt": "Part\n{# comment\n",
            },
            "Missing end of comment tag (docs/_part.txt, line 2)",
        ),
        (
            {"page.template": 'Hello\n{% include "_missing.txt" %}\n'},
            "Template '_missing.txt' not found (docs/page.template, line 2)",
        ),
        (
            {"page.template": 'Hello\n{% include ["_a.txt", "_b.txt"] %}\n'},
            "none of the templates given were found: _a.txt, _b.txt "
            "(docs/page.template, line 2)",
        ),
        (
            {"page.template": "Hello\n{{ 1 / 0 }}\n"},
            "ZeroDivisionError: division by zero (docs/page.template, line 2)",
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
    A template that can't be rendered must be refused, naming the template that failed,
    relative to the template's directory as it was given, and the line.
    """
    (project / "docs").mkdir()

    for name, body in files.items():
        (project / "docs" / name).write_text(body, encoding="utf-8")

    _assert_fails(
        capsys,
        project,
        ["docs/page.template", "values.yaml", "out.md"],
        message,
    )


@mark.parametrize(
    ("template", "message"),
    [
        ("missing.template", "No such file or directory: 'missing.template'"),
        (".", "Is a directory: '.'"),
        ("docs", "Is a directory: 'docs'"),
        ("docs/..", "Is a directory: 'docs/..'"),
        ("README.template/", "Not a directory: 'README.template/'"),
        ("README.template/.", "Not a directory: 'README.template/.'"),
        ("fifo", "Template 'fifo' is not a regular file"),
    ],
    ids=[
        "missing",
        "working directory",
        "directory",
        "parent directory",
        "trailing separator",
        "trailing dot",
        "fifo",
    ],
)
def test_refuses_template_that_is_not_a_file(
    template: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A template that isn't a file must be refused, naming it as it was given."""
    (project / "docs").mkdir()

    if template == "fifo":
        # Not every platform has FIFOs, like Windows.
        if not hasattr(os, "mkfifo"):
            skip("This platform doesn't have FIFOs")

        os.mkfifo(project / "fifo")

    _assert_fails(capsys, project, [template, "values.yaml", "out.md"], message)


def test_refuses_template_that_leads_outside_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template that's a symlink to a file outside its own directory must be refused,
    because its includes are resolved from the directory of the symlink.
    """
    (project / "docs").mkdir()
    (project / "docs" / "link.template").symlink_to(project / "README.template")

    _assert_fails(
        capsys,
        project,
        ["docs/link.template", "values.yaml", "out.md"],
        f"Template 'docs/link.template' leads to "
        f"'{realpath(project / 'README.template')}', outside its directory 'docs'",
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
        ("docs", "Is a directory: 'docs'"),
    ],
    ids=[
        "missing",
        "directory",
    ],
)
def test_refuses_values_that_cannot_be_read(
    values: str,
    message: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A values file that can't be read must be refused, naming it as it was given."""
    (project / "docs").mkdir()
    _assert_fails(capsys, project, ["README.template", values, "README.md"], message)


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


def test_stops_quietly_when_interrupted(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """An interrupt must stop `platen` quietly, with exit code 130."""

    def press_file(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file)

    assert main(["README.template", "values.yaml", "README.md"]) == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
