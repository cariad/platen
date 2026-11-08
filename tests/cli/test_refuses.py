import os
from errno import EIO
from os import R_OK, access, link, strerror
from os.path import exists, isabs, realpath
from pathlib import Path

from jinja2 import TemplateSyntaxError
from pytest import CaptureFixture, MonkeyPatch, mark, skip
from rootle.testing import (
    can_chmod_deny_listing,
    can_chmod_deny_writing,
)

from platen.cli import main
from tests.cli.helpers import assert_fails


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

    assert_fails(
        capsys,
        project,
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "templates/build",
        ],
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

    # `assert_fails` would see `a.md`'s result as a change.
    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: error: 'missing' is undefined "
        f"({realpath(templates / 'b.md')}, line 1)\n"
    )

    build = project / "build"
    result = [path.relative_to(build) for path in build.rglob("*")]
    expect = [Path("a.md")]

    assert result == expect

    result = (build / "a.md").read_bytes()

    assert result == b"A\n"


def test_refuses_directory_that_cannot_be_read(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A directory that can't be listed must be refused, naming it absolutely, before
    anything is pressed.
    """
    if not can_chmod_deny_listing(project):
        skip("Permissions don't stop this user from reading directories")

    directory = project / "templates"
    directory.mkdir()
    (directory / "a.md").write_text("A\n", encoding="utf-8")
    directory.chmod(0o300)

    try:
        # `assert_fails` can't snapshot a directory that can't be read.
        exit_code = main(
            [
                "--templates",
                "templates",
                "--values",
                "values.yaml",
                "--out",
                "build",
            ],
        )

        assert exit_code == 1

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
        # A symlink to a FIFO, which the test makes beside the templates directory.
        (
            "pipe",
            "../fifo",
            "Template '{templates}/pipe' can't be pressed: it isn't a regular file",
        ),
        # A walk never steps into a symlink, even to a directory like `sub`, so it's
        # taken as a template.
        (
            "linked",
            "sub",
            "Template '{templates}/linked' can't be pressed: it isn't a regular file",
        ),
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

    assert_fails(
        capsys,
        project,
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
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
    if not can_chmod_deny_writing(project):
        skip("Permissions don't stop this user from writing files")

    directory = project / "ro"
    directory.mkdir()
    directory.chmod(0o555)

    try:
        assert_fails(
            capsys,
            project,
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "ro/out.md",
            ],
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "existing",
        ],
        "Is a directory: 'existing'",
    )


@mark.parametrize(
    ("error", "message"),
    [
        (OSError(EIO, strerror(EIO)), strerror(EIO)),
        (OSError(EIO, strerror(EIO), "out.md"), f"{strerror(EIO)}: 'out.md'"),
        # An error that names two paths, like a failed rename. The `None` is the
        # Windows error code, which `OSError` takes before the second path.
        (
            OSError(EIO, strerror(EIO), "README.template", None, "out.md"),
            f"{strerror(EIO)}: 'README.template' -> 'out.md'",
        ),
        # An error that the operating system didn't describe is named by its type.
        (OSError("Disk on fire"), "OSError: Disk on fire"),
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

    monkeypatch.setattr("platen.Platen.press_file", press_file)

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
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
    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.template",
        ],
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
    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "values.yaml",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
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

    monkeypatch.setattr("platen.Platen.press_file", press_file)
    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
        "Broken",
    )


@mark.parametrize(
    ("template", "message"),
    [
        (
            "missing.template",
            "No such file or directory: 'missing.template'",
        ),
        # `README.template` is there, but the trailing separator asks for a directory.
        (
            "README.template/",
            "Not a directory: 'README.template/'",
        ),
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
    assert_fails(
        capsys,
        project,
        [
            "--template",
            template,
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
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

        # `assert_fails` can't snapshot a file that can't be read.
        exit_code = main(
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
            ],
        )

        assert exit_code == 1

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
        # Reading an attribute of an undefined value fails in the include, so the
        # include is named, rather than the template that includes it.
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
        # Jinja names an unclosed comment's file only in the error, not its traceback.
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
        # When none of a list of includes are found, Jinja raises `TemplatesNotFound`,
        # whose own message names only the includes, so it's kept.
        (
            {"page.template": 'Hello\n{% include ["_a.txt", "_b.txt"] %}\n'},
            "none of the templates given were found: _a.txt, _b.txt "
            "({docs}/page.template, line 2)",
        ),
        # Python's own error, rather than Jinja's, is named by its type.
        (
            {"page.template": "Hello\n{{ 1 / 0 }}\n"},
            "ZeroDivisionError: division by zero ({docs}/page.template, line 2)",
        ),
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "docs/page.template",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "fifo",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "docs/_legacy.txt",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "docs/link.template",
            "--values",
            "values.yaml",
            "--out",
            "out.md",
        ],
        f"Template '{realpath(project / 'README.template')}' is not within the "
        f"templates directory '{realpath(project / 'docs')}'",
    )


@mark.parametrize(
    ("body", "message"),
    [
        # A list isn't a mapping, and that error quotes the name like `repr` does, in
        # double quotes because of its `'`.
        (
            "- platen\n",
            'Values file "it\'s\\nvalues.yaml" must hold a mapping of names to values',
        ),
        # A YAML syntax error names the file unquoted, beside the line and column.
        (
            "name: platen: x\n",
            "mapping values are not allowed here "
            "(it's\\nvalues.yaml, line 1, column 13)",
        ),
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "it's\nvalues.yaml",
            "--out",
            "README.md",
        ],
        message,
    )


@mark.parametrize(
    "body",
    [
        "- platen\n",
        "platen\n",
        "42\n",
        # YAML reads this as a date, rather than a string.
        "2026-10-08\n",
    ],
)
def test_refuses_values_that_are_not_mapping(
    body: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """Values that aren't a mapping must be refused."""
    (project / "values.yaml").write_text(body, encoding="utf-8")

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
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
        # YAML doesn't allow a tab to indent.
        (
            b"name:\n\tstatus: passing\n",
            "while scanning for the next token, found character '\\t' that cannot "
            "start any token (values.yaml, line 2, column 1)",
        ),
        # ruamel.yaml doesn't mark the line of a byte that isn't UTF-8, so its own
        # description, with a position, is given instead.
        (
            b"name: \xff\n",
            "unacceptable character #x00ff: invalid start byte "
            'in "values.yaml", position 6',
        ),
        # ruamel.yaml asserts a document's YAML version, rather than raising an error
        # that says where, so only the file is named.
        (
            b"%YAML 1.3\n---\nname: platen\n",
            "version minor part can only be 2 or 1, got (1, 3) (values.yaml)",
        ),
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
        message,
    )


@mark.parametrize(
    ("values", "message"),
    [
        ("missing.yaml", "No such file or directory: 'missing.yaml'"),
        ("{cwd}/missing.yaml", "No such file or directory: '{cwd}/missing.yaml'"),
        ("docs", "Is a directory: 'docs'"),
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            values.format(cwd=project),
            "--out",
            "README.md",
        ],
        message.format(cwd=project),
    )


@mark.parametrize(
    ("body", "key"),
    [
        ("1: one\n", "1"),
        # YAML reads `true` as a boolean, and the key is named as Python shows it.
        ("true: one\n", "True"),
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

    assert_fails(
        capsys,
        project,
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
        f"Values file 'values.yaml' has a key that isn't a string: {key}",
    )
