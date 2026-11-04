import os
import sys
from collections.abc import Callable, Iterator
from errno import EIO
from importlib.metadata import PackageNotFoundError, entry_points, version
from itertools import count
from os import R_OK, PathLike, access, link, strerror
from os.path import exists, isabs, realpath
from pathlib import Path
from queue import Empty, Queue
from shutil import copymode
from signal import SIGINT
from stat import S_IMODE
from subprocess import PIPE, Popen, run
from threading import Thread
from time import sleep
from typing import cast

from jinja2 import TemplateSyntaxError
from pytest import CaptureFixture, MonkeyPatch, fail, fixture, mark, raises, skip
from rootle.testing import (
    can_chmod_deny_listing,
    can_chmod_deny_search,
    can_chmod_deny_writing,
)
from watchfiles import Change

from platen import Platen
from platen.cli import main
from tests.edits import edit
from tests.snapshots import snapshot

PRESSED_DIRECTORY = "platen: Pressed 'templates' to 'build'\n"

PRESSED_FILE = "platen: Pressed 'README.template' to 'README.md'\n"

USAGE = (
    "usage: platen [-h] [--version] --template PATH --values PATH --out PATH\n"
    "              [--watch]\n"
)

WATCHING = "platen: Watching for changes. Press Ctrl+C to stop.\n"


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

    exit_code = main(arguments)

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"platen: error: {message}\n"

    result = snapshot(directory)

    assert result == before


def _create(
    directory: Path,
    files: dict[str, str],
    links: dict[str, str] | None = None,
) -> None:
    """
    Create files within a directory, and then symlinks, creating missing directories
    and replacing whatever is already at each symlink's path.
    """
    for name, body in files.items():
        path = directory / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(body, encoding="utf-8")

    for name, target in (links or {}).items():
        path = directory / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.unlink(missing_ok=True)
        path.symlink_to(target)


def _replace(path: Path, body: str) -> None:
    """
    Replace a file with a new one, like an editor that saves atomically, so that its
    path names a different file.
    """
    new = path.with_suffix(".new")
    new.write_text(body, encoding="utf-8")
    os.replace(new, path)


def _rewrite_keeping_times(path: Path, body: str) -> None:
    """
    Rewrite a file at the same size and put its times back, like `cp -p` or `touch -r`
    does, so that only its change time tells. Write until that's moved on, in case the
    clock is too coarse to tell this write from the last.
    """
    before = path.stat()

    while path.stat().st_ctime_ns == before.st_ctime_ns:
        path.write_text(body, encoding="utf-8")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def _watching(
    monkeypatch: MonkeyPatch,
    *steps: Callable[[], object],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    """
    Replace watchfiles' `watch` with a fake that takes the next step at each wake,
    rather than waiting for a change, and return the paths and options that each watch
    started with. No time passes. A step that returns a set wakes with those events,
    and any other wakes with none.

    Like watchfiles, a watch starts at its first wake, so a step that raises there, like
    `FileNotFoundError` for a missing path, fails the start. A watch that restarts
    carries on with the next step. Once every step is taken, the next wake raises
    `KeyboardInterrupt`, like Ctrl+C.

    Platen starts a new watch after every press, so a watch that keeps being created
    without waking means that every press causes another, forever. That fails the test,
    rather than hanging it.
    """
    created = count()
    started: list[tuple[tuple[str, ...], dict[str, object]]] = []
    remaining = iter(steps)

    def wakes(
        paths: tuple[str, ...],
        options: dict[str, object],
    ) -> Iterator[set[object]]:
        started.append((paths, options))

        for step in remaining:
            events = step()
            yield cast(set[object], events) if isinstance(events, set) else set()

        raise KeyboardInterrupt

    def watch(*paths: str, **options: object) -> Iterator[set[object]]:
        # Each step can cause a press, a restart, or both, and the first watch starts
        # after the first press.
        if next(created) > 2 * len(steps) + 2:
            fail("Platen kept pressing without being woken")

        return wakes(paths, options)

    monkeypatch.setattr("watchfiles.watch", watch)
    return started


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
    result = script.load()

    assert result is main


def test_help(capsys: CaptureFixture[str], monkeypatch: MonkeyPatch) -> None:
    """
    `--help` must print usage to stdout, naming both of the template's options and
    `--watch`, and exit with 0.
    """
    # argparse wraps its usage to the terminal's width, and `USAGE` is 80 columns.
    monkeypatch.setenv("COLUMNS", "80")

    # Python 3.14's argparse colours its output when the environment asks for colour.
    monkeypatch.setenv("PYTHON_COLORS", "0")

    with raises(SystemExit) as ex:
        main(
            [
                "--help",
            ],
        )

    assert ex.value.code == 0

    captured = capsys.readouterr()
    assert captured.out.startswith(USAGE)
    assert "\n  --template, --templates PATH\n" in captured.out
    assert "\n  --watch " in captured.out
    assert captured.err == ""


@mark.parametrize(
    "template",
    [
        "templates",
        "templates/",
        "./templates",
        "{cwd}/templates",
        # A symlink to `templates`, which the test creates beside it.
        "link",
        "templates/sub/..",
    ],
)
def test_presses_directory(
    template: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen --templates templates --values values.yaml --out build` must press every
    file within the directory to the same path within the output, however the
    directory is named, print nothing, and exit with 0.

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

    exit_code = main(
        [
            "--templates",
            template.format(cwd=project),
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }
    expect = {
        Path("_badge.txt"): b"Badge: passing\n",
        Path("index.md"): b"# platen\n\nBadge: passing\n",
        Path("sub/page.md"): b"Badge: passing\n",
    }

    assert pressed == expect


def test_presses_empty_values(capsys: CaptureFixture[str], project: Path) -> None:
    """An empty values file must be read as no values, rather than refused."""
    (project / "doc.template").write_text("Hello, world!\n", encoding="utf-8")
    (project / "empty.yaml").write_text("", encoding="utf-8")

    exit_code = main(
        [
            "--template",
            "doc.template",
            "--values",
            "empty.yaml",
            "--out",
            "doc.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    result = (project / "doc.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


def test_presses_readme_next_to_template(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen --template README.template --values values.yaml --out README.md` must press
    the README next to its template, resolving its include from the template's
    directory, print nothing, and exit with 0.
    """
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

    assert exit_code == 0

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

    exit_code = main(
        [
            "--template",
            "link.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

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

    exit_code = main(
        [
            "--template",
            "docs/page.template",
            "--values",
            "values.yaml",
            "--out",
            "build/site/index.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    pressed = (project / "build" / "site" / "index.md").read_text(encoding="utf-8")
    assert pressed == "Hello from platen\n"


@mark.parametrize(
    ("ignore", "expect"),
    [
        # Without a `.platenignore` file, the values file is pressed like any other
        # template, so its own `{{ name }}` is rendered.
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

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "templates/values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

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

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# a\n\nBuild: passing\n"


@mark.parametrize(
    ("option", "template", "output"),
    [
        ("--template", "docs", "build"),
        ("--templates", "docs/page.md", "build/page.md"),
    ],
)
def test_presses_with_either_template_option(
    option: str,
    template: str,
    output: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """`--template` must also press a directory, and `--templates` a file."""
    (project / "docs").mkdir()
    (project / "docs" / "page.md").write_text("# {{ name }}\n", encoding="utf-8")

    exit_code = main(
        [
            option,
            template,
            "--values",
            "values.yaml",
            "--out",
            output,
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    pressed = (project / "build" / "page.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n"


def test_presses_with_options_in_any_order(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """The options must press the same in any order."""
    exit_code = main(
        [
            "--out",
            "README.md",
            "--values",
            "values.yaml",
            "--template",
            "README.template",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_presses_without_importing_watchfiles(project: Path) -> None:
    """
    A press without `--watch` must never import watchfiles, or the anyio that it
    imports, which would slow down every press.
    """
    # Only a fresh interpreter shows what a press imports, because this one imports
    # watchfiles for other tests.
    script = (
        "import sys\n"
        "from platen.cli import main\n"
        "exit_code = main()\n"
        "imported = {'anyio', 'watchfiles'} & sys.modules.keys()\n"
        "sys.exit(99 if imported else exit_code)\n"
    )

    result = run(
        [
            sys.executable,
            "-c",
            script,
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
        capture_output=True,
        check=False,
        cwd=project,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


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

    _assert_fails(
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

    _assert_fails(
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

    # `_assert_fails` would see `a.md`'s result as a change.
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
        # `_assert_fails` can't snapshot a directory that can't be read.
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

    _assert_fails(
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
        _assert_fails(
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

    _assert_fails(
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

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file)

    _assert_fails(
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
    _assert_fails(
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
    _assert_fails(
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

    _assert_fails(
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

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file)
    _assert_fails(
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
    _assert_fails(
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

        # `_assert_fails` can't snapshot a file that can't be read.
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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

    _assert_fails(
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


@mark.parametrize(
    ("arguments", "message"),
    [
        (
            [],
            "the following arguments are required: --template/--templates, --values, "
            "--out",
        ),
        # The paths without their options, as Platen's arguments were once given.
        (
            [
                "README.template",
                "values.yaml",
                "README.md",
            ],
            "the following arguments are required: --template/--templates, --values, "
            "--out",
        ),
        (
            [
                "--values",
                "values.yaml",
                "--out",
                "README.md",
            ],
            "the following arguments are required: --template/--templates",
        ),
        (
            [
                "--template",
                "README.template",
                "--out",
                "README.md",
            ],
            "the following arguments are required: --values",
        ),
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
            ],
            "the following arguments are required: --out",
        ),
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
            ],
            "argument --out: expected one argument",
        ),
        # Prefixes of options aren't accepted, so `--values` is still missing.
        (
            [
                "--template",
                "README.template",
                "--val",
                "values.yaml",
                "--out",
                "x.md",
            ],
            "the following arguments are required: --values",
        ),
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
                "--force",
            ],
            "unrecognized arguments: --force",
        ),
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
                "extra",
            ],
            "unrecognized arguments: extra",
        ),
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
                "--watch=yes",
            ],
            "argument --watch: ignored explicit argument 'yes'",
        ),
    ],
)
def test_refuses_wrong_arguments(
    arguments: list[str],
    message: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    The wrong number of arguments, or a value given to `--watch`, must print usage and
    an error to stderr, exit with 2, and write nothing.
    """
    # argparse wraps its usage to the terminal's width, and `USAGE` is 80 columns.
    monkeypatch.setenv("COLUMNS", "80")

    # Python 3.14's argparse colours its output when the environment asks for colour.
    monkeypatch.setenv("PYTHON_COLORS", "0")

    before = snapshot(project)

    with raises(SystemExit) as ex:
        main(arguments)

    assert ex.value.code == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{USAGE}platen: error: {message}\n"

    result = snapshot(project)

    assert result == before


@mark.parametrize(
    ("option", "template", "method"),
    [
        (
            "--templates",
            ".",
            "press",
        ),
        (
            "--template",
            "README.template",
            "press_file",
        ),
    ],
)
def test_stops_quietly_when_interrupted(
    option: str,
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

    exit_code = main(
        [
            option,
            template,
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_version(capsys: CaptureFixture[str]) -> None:
    """
    `--version` must print the installed version to stdout and exit with 0, without
    the other arguments.
    """
    with raises(SystemExit) as ex:
        main(
            [
                "--version",
            ],
        )

    assert ex.value.code == 0

    captured = capsys.readouterr()
    assert captured.out == f"platen {version('platen')}\n"
    assert captured.err == ""


def test_version_without_metadata(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """
    `--version` must report an unknown version, rather than fail, when Platen's
    package metadata isn't installed.
    """

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("platen.cli.version", missing)

    with raises(SystemExit) as ex:
        main(
            [
                "--version",
            ],
        )

    assert ex.value.code == 0

    captured = capsys.readouterr()
    assert captured.out == "platen unknown\n"
    assert captured.err == ""


@mark.parametrize(
    ("template", "files"),
    [
        ("docs", {}),
        ("docs/", {}),
        ("docs", {".platenignore": "*.md\n", "page.md": "Hello\n"}),
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

    exit_code = main(
        [
            "--templates",
            template,
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: warning: Nothing to press in '{template}': it's empty, or "
        "everything in it is ignored\n"
    )

    result = snapshot(project)

    assert result == before


@mark.parametrize(
    "error",
    [
        PermissionError("Permission denied (os error 13)"),
        OSError(
            'OS file watch limit reached. about ["/docs"] '
            '(Error { kind: MaxFilesWatch, paths: ["/docs"] })',
        ),
    ],
)
def test_watch_fails_when_changes_cannot_be_watched(
    error: Exception,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must stop with exit code 1 when watchfiles can't watch for
    changes, like when permissions deny it or the operating system's limit is reached,
    printing watchfiles' own description of the error after the press.
    """

    def fail() -> None:
        raise error

    _watching(
        monkeypatch,
        fail,
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"{PRESSED_FILE}{WATCHING}platen: error: Can't watch for changes: {error}\n"
    )

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# platen\n\nBuild: passing\n"


def test_watch_fails_when_working_directory_is_deleted(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must stop with exit code 1 when the working directory is deleted
    while it watches, because the relative paths that it watches lead nowhere.
    """
    working_dir = project / "gone"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    # The project directory holds the template, so it's watched, and its watcher says
    # that the working directory within it was deleted.
    deleted = realpath(working_dir)

    def delete() -> set[tuple[Change, str]]:
        working_dir.rmdir()
        return {(Change.deleted, deleted)}

    _watching(monkeypatch, delete)

    exit_code = main(
        [
            "--template",
            "../README.template",
            "--values",
            "../values.yaml",
            "--out",
            "../README.md",
            "--watch",
        ],
    )

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: Pressed '../README.template' to '../README.md'\n"
        f"{WATCHING}"
        "platen: error: The working directory no longer exists, so changes can't be "
        "watched\n"
    )

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# platen\n\nBuild: passing\n"


def test_watch_finds_directories_again_after_waiting_when_watchers_keep_failing(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must wait 5 seconds before finding the directories to watch again
    when a watcher that replaced one that couldn't find a directory can't find one
    either, so that watchers can't start as fast as they fail.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("platen.cli.sleep", sleeps.append)

    def fail() -> None:
        raise FileNotFoundError("No such file or directory (os error 2)")

    started = _watching(
        monkeypatch,
        fail,
        fail,
        lambda: None,
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root,),
        (root,),
        (root,),
    ]

    # The first watcher replaced none, so only the second waited.
    assert sleeps == [5]


def test_watch_finds_directories_again_when_one_disappears(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must find the directories to watch again, rather than fail, when
    one disappears after it's found but before watching starts, like during a checkout.
    """
    templates = project / "templates"
    (templates / "sub").mkdir(parents=True)
    (templates / "index.md").write_text("# {{ name }}\n", encoding="utf-8")

    def disappear() -> None:
        (templates / "sub").rmdir()

        # watchfiles raises this as it starts, for a path that's missing.
        raise FileNotFoundError("No such file or directory (os error 2)")

    started = _watching(monkeypatch, disappear)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_DIRECTORY}{WATCHING}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root, f"{root}/templates", f"{root}/templates/sub"),
        (root, f"{root}/templates"),
    ]

    result = (project / "build" / "index.md").read_bytes()

    assert result == b"# platen\n"


@mark.parametrize(
    ("template", "output", "files", "change"),
    [
        # The output is beside its template, in a watched directory, but isn't read.
        (
            "README.template",
            "README.md",
            {},
            lambda: edit(Path("README.md"), "Edited\n"),
        ),
        (
            "README.template",
            "README.md",
            {},
            lambda: Path("README.md").unlink(),
        ),
        (
            "README.template",
            "README.md",
            {},
            lambda: Path("notes.txt").write_text("Notes\n", encoding="utf-8"),
        ),
        (
            "templates",
            "build",
            {
                "templates/.platenignore": "*.log\n",
                "templates/index.md": "# {{ name }}\n",
                "templates/notes.log": "Notes\n",
            },
            lambda: edit(Path("templates/notes.log"), "Edited\n"),
        ),
        # `.platenignore` matters only for what the walk finds, which a comment added
        # to it doesn't change.
        (
            "templates",
            "build",
            {
                "templates/.platenignore": "*.log\n",
                "templates/index.md": "# {{ name }}\n",
                "templates/notes.log": "Notes\n",
            },
            lambda: edit(Path("templates/.platenignore"), "# Logs\n*.log\n"),
        ),
        # An include named with a null character, which `ignore missing` skips.
        (
            "README.template",
            "README.md",
            {
                "README.template": "# {{ name }}\n{% include part ignore missing %}",
                "values.yaml": 'name: platen\npart: "_\\0.txt"\n',
            },
            # Nothing can ever be found at a path that holds a null character, so
            # nothing can change it.
            lambda: None,
        ),
    ],
)
def test_watch_ignores_files_that_press_does_not_read(
    template: str,
    output: str,
    files: dict[str, str],
    change: Callable[[], object],
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must not press again when a file changes that the last press
    didn't read, even beside the template, like the press's own output, an ignored
    file, or a comment in a `.platenignore` file, or for an include that can never be
    found.
    """
    _create(project, files)
    changed: list[dict[Path, tuple[bytes, int] | None]] = []

    def change_and_snapshot() -> None:
        change()
        changed.append(snapshot(project))

    _watching(monkeypatch, change_and_snapshot)

    exit_code = main(
        [
            "--template",
            template,
            "--values",
            "values.yaml",
            "--out",
            output,
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"platen: Pressed '{template}' to '{output}'\n{WATCHING}"

    # Nothing was written after the change.
    expect = [snapshot(project)]

    assert changed == expect


@mark.parametrize(
    ("template", "output", "files", "links", "included", "expect"),
    [
        # The template includes its own output, so every press changes what it read.
        (
            "README.template",
            "README.md",
            {
                "README.template": (
                    '# {{ name }}\n{% include "README.md" ignore missing %}'
                ),
            },
            {},
            "README.md",
            b"# other\n# platen\n",
        ),
        # `build/index.md` is a symlink to `_part.txt`, which the template `index.md`
        # includes, so every press writes to an include.
        (
            "templates",
            "build",
            {
                "templates/.platenignore": "_*\n",
                "templates/_part.txt": "Part\n",
                "templates/index.md": '{{ name }}: {% include "_part.txt" %}',
            },
            {"build/index.md": "../templates/_part.txt"},
            "templates/_part.txt",
            b"other: platen: Part\n",
        ),
    ],
)
def test_watch_ignores_output_that_template_includes(
    template: str,
    output: str,
    files: dict[str, str],
    links: dict[str, str],
    included: str,
    expect: bytes,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must not watch a template that a press writes to, like an output
    that the template includes, or a template that a symlink within the output leads
    to, or every press would cause another, forever.
    """
    _create(project, files, links)
    _watching(
        monkeypatch,
        lambda: edit(project / "values.yaml", "name: other\nstatus: passing\n"),
    )

    exit_code = main(
        [
            "--template",
            template,
            "--values",
            "values.yaml",
            "--out",
            output,
            "--watch",
        ],
    )

    assert exit_code == 130

    pressed = f"platen: Pressed '{template}' to '{output}'\n"

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{pressed}{WATCHING}{pressed}"

    result = (project / included).read_bytes()

    assert result == expect


def test_watch_ignores_output_that_template_includes_when_press_fails(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must not watch a template that a press wrote to even when the
    press failed after writing it, or every press would cause another, forever.
    """
    _create(
        project,
        {
            "templates/.platenignore": "_*\n",
            "templates/_part.txt": "Part\n",
            "templates/a.md": '{% include "_part.txt" %}',
            # The walk finds `b.md` after `a.md`, so the press fails after writing.
            "templates/b.md": "{{ missing }}\n",
        },
        {"build/a.md": "../templates/_part.txt"},
    )

    _watching(monkeypatch)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    templates = realpath(project / "templates")

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: error: 'missing' is undefined ({templates}/b.md, line 1)\n{WATCHING}"
    )

    result = (project / "templates" / "_part.txt").read_bytes()

    assert result == b"Part\n"


@mark.parametrize(
    ("change", "name"),
    [
        # Polling sees what's in a watched directory change.
        (Change.modified, "parts"),
        (Change.added, "parts/other.txt"),
        (Change.deleted, "parts/other.txt"),
    ],
)
def test_watch_keeps_watcher_when_event_does_not_replace_directory(
    change: Change,
    name: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must keep its watcher when an event names anything but a watched
    directory that was deleted or added, like a file within one, or a watched directory
    that was modified, rather than start a new one at every wake.
    """
    _create(
        project,
        {
            "README.template": (
                '# {{ name }}\n{% include "parts/_part.txt" ignore missing %}'
            ),
        },
    )

    (project / "parts").mkdir()

    started = _watching(
        monkeypatch,
        lambda: {(change, realpath(project / name))},
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root, f"{root}/parts"),
    ]


@mark.parametrize(
    ("files", "links", "hidden", "fix", "message"),
    [
        (
            {"_badge.txt": "Build: {{ missing }}\n"},
            {},
            [],
            lambda: edit(Path("_badge.txt"), "Build: {{ status }}\n"),
            "'missing' is undefined ({project}/_badge.txt, line 1)",
        ),
        (
            {"_badge.txt": "Build: {% if %}\n"},
            {},
            [],
            lambda: edit(Path("_badge.txt"), "Build: {{ status }}\n"),
            "Expected an expression, got 'end of statement block' "
            "({project}/_badge.txt, line 1)",
        ),
        # A symlink that leads back to itself, so nothing can be found there.
        (
            {"_badge.new": "Build: {{ status }}\n"},
            {"_badge.txt": "_badge.txt"},
            [],
            lambda: os.replace("_badge.new", "_badge.txt"),
            "Template '_badge.txt' not found ({project}/README.template, line 3)",
        ),
        # The include is in `private`, which can't be read until the fix.
        (
            {
                "README.template": (
                    '# {{ name }}\n\n{% include "private/_badge.txt" %}\n'
                ),
                "private/_badge.txt": "Build: {{ status }}\n",
            },
            {},
            ["private"],
            lambda: Path("private").chmod(0o755),
            "Template 'private/_badge.txt' not found "
            "({project}/README.template, line 3)",
        ),
    ],
)
def test_watch_keeps_watching_after_press_fails(
    files: dict[str, str],
    links: dict[str, str],
    hidden: list[str],
    fix: Callable[[], object],
    message: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must keep watching when a press fails, printing the error as a
    press without `--watch` does, and press again once the template is fixed, like an
    include that can't be rendered, loops, or is in a directory that can't be read.
    """
    if hidden and not can_chmod_deny_search(project):
        skip("Permissions don't stop this user from searching directories")

    _create(project, files, links)

    for name in hidden:
        (project / name).chmod(0)

    try:
        _watching(monkeypatch, fix)

        exit_code = main(
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
                "--watch",
            ],
        )

        assert exit_code == 130

        captured = capsys.readouterr()
        assert captured.out == ""

        assert captured.err == (
            f"platen: error: {message.format(project=realpath(project))}\n"
            f"{WATCHING}{PRESSED_FILE}"
        )

        pressed = (project / "README.md").read_bytes()
        assert pressed == b"# platen\n\nBuild: passing\n"

    finally:
        for name in hidden:
            (project / name).chmod(0o755)


def test_watch_presses_again_after_change_made_while_pressing(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again straight away, rather than wait to be woken, when
    the values change during a press, after the press read them.
    """
    press_file = Platen.press_file
    presses = count(1)

    def press_file_then_change_values(
        self: Platen,
        template: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        press_file(self, template, destination)

        # Only change the values during the press that the wake starts, which has read
        # them by now.
        if next(presses) == 2:
            edit(project / "values.yaml", "name: during\nstatus: passing\n")

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file_then_change_values)

    _watching(
        monkeypatch,
        lambda: edit(project / "values.yaml", "name: before\nstatus: passing\n"),
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}{PRESSED_FILE}"

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# during\n\nBuild: passing\n"


def test_watch_presses_again_after_include_changes_between_templates(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again straight away when an include changes after a
    directory press pressed one template that includes it, but before it pressed
    another, which reads the include again, because the first template's output still
    has the include as it was.
    """
    _create(
        project,
        {
            "templates/.platenignore": "_*\n",
            "templates/_badge.txt": "Build: {{ status }}\n",
            "templates/a.md": '{% include "_badge.txt" %}',
            "templates/b.md": '{% include "_badge.txt" %}',
        },
    )

    copies = count(1)

    def copymode_then_change_include(
        source: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        copymode(source, destination)

        # Only change the include once `a.md` is pressed, during the first press, so
        # that Jinja reads it again for `b.md`.
        if next(copies) == 1:
            edit(project / "templates" / "_badge.txt", "Build: {{ status }}!\n")

    monkeypatch.setattr("platen.platen.copymode", copymode_then_change_include)

    _watching(monkeypatch)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_DIRECTORY}{WATCHING}{PRESSED_DIRECTORY}"

    pressed = (project / "build" / "a.md").read_bytes()
    assert pressed == b"Build: passing!\n"


def test_watch_presses_again_after_include_changes_while_pressing(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again straight away, rather than wait to be woken, when
    an include changes during the first press, after the press read it, even though it
    wasn't looked at before the press.
    """
    press_file = Platen.press_file
    presses = count(1)

    def press_file_then_change_include(
        self: Platen,
        template: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        press_file(self, template, destination)

        # Only change the include during the first press, which has read it by now.
        if next(presses) == 1:
            edit(project / "_badge.txt", "Build: {{ status }}!\n")

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file_then_change_include)

    _watching(monkeypatch)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# platen\n\nBuild: passing!\n"


@mark.parametrize(
    ("files", "first", "change", "expect"),
    [
        (
            {},
            PRESSED_DIRECTORY,
            lambda: Path("templates/new.md").write_text("New\n", encoding="utf-8"),
            {
                Path("index.md"): b"# platen\n\nBuild: passing\n",
                Path("new.md"): b"New\n",
            },
        ),
        # A deleted template's output is left behind.
        (
            {"templates/old.md": "Old\n"},
            PRESSED_DIRECTORY,
            lambda: Path("templates/old.md").unlink(),
            {
                Path("index.md"): b"# platen\n\nBuild: passing\n",
                Path("old.md"): b"Old\n",
            },
        ),
        # A renamed template's old output is left behind.
        (
            {"templates/old.md": "Old\n"},
            PRESSED_DIRECTORY,
            lambda: Path("templates/old.md").rename("templates/new.md"),
            {
                Path("index.md"): b"# platen\n\nBuild: passing\n",
                Path("new.md"): b"Old\n",
                Path("old.md"): b"Old\n",
            },
        ),
        (
            {},
            PRESSED_DIRECTORY,
            lambda: edit(
                Path("templates/index.md"),
                '# {{ name }}!\n\n{% include "_badge.txt" %}\n',
            ),
            {
                Path("index.md"): b"# platen!\n\nBuild: passing\n",
            },
        ),
        (
            {},
            PRESSED_DIRECTORY,
            lambda: edit(Path("values.yaml"), "name: other\nstatus: passing\n"),
            {
                Path("index.md"): b"# other\n\nBuild: passing\n",
            },
        ),
        # `_badge.txt` is ignored, but `index.md` includes it.
        (
            {},
            PRESSED_DIRECTORY,
            lambda: edit(Path("templates/_badge.txt"), "Build: {{ status }}!\n"),
            {
                Path("index.md"): b"# platen\n\nBuild: passing!\n",
            },
        ),
        # A negated pattern stops ignoring `_badge.txt`, so it's pressed itself too.
        (
            {},
            PRESSED_DIRECTORY,
            lambda: edit(Path("templates/.platenignore"), "_*\n!_badge.txt\n"),
            {
                Path("_badge.txt"): b"Build: passing\n",
                Path("index.md"): b"# platen\n\nBuild: passing\n",
            },
        ),
        # The first press fails on `broken.md`, until `.platenignore` ignores it.
        (
            # The walk finds `broken.md` first, so the press fails before `index.md`.
            {"templates/broken.md": "{{ missing }}\n"},
            "platen: error: 'missing' is undefined ({templates}/broken.md, line 1)\n",
            lambda: edit(Path("templates/.platenignore"), "_*\nbroken.md\n"),
            {
                Path("index.md"): b"# platen\n\nBuild: passing\n",
            },
        ),
    ],
)
def test_watch_presses_again_when_directory_changes(
    files: dict[str, str],
    first: str,
    change: Callable[[], object],
    expect: dict[Path, bytes],
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press a directory again when the templates that a walk finds
    change, even when only their names do, or when the values or an ignored template
    that one includes changes, leaving the outputs of templates that are gone.
    """
    templates = project / "templates"

    _create(
        project,
        {
            "templates/.platenignore": "_*\n",
            "templates/_badge.txt": "Build: {{ status }}\n",
            "templates/index.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            **files,
        },
    )

    _watching(monkeypatch, change)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"{first.format(templates=realpath(templates))}{WATCHING}{PRESSED_DIRECTORY}"
    )

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }

    assert pressed == expect


@mark.parametrize(
    ("files", "links", "change", "expect"),
    [
        (
            {},
            {},
            lambda: edit(
                Path("README.template"),
                '# {{ name }}!\n\n{% include "_badge.txt" %}\n',
            ),
            b"# platen!\n\nBuild: passing\n",
        ),
        (
            {},
            {},
            lambda: edit(Path("_badge.txt"), "Build: {{ status }}!\n"),
            b"# platen\n\nBuild: passing!\n",
        ),
        (
            {},
            {},
            lambda: edit(Path("values.yaml"), "name: other\nstatus: passing\n"),
            b"# other\n\nBuild: passing\n",
        ),
        # The values keep their size, so only their later modification time tells.
        (
            {},
            {},
            lambda: edit(Path("values.yaml"), "name: platen\nstatus: failing\n"),
            b"# platen\n\nBuild: failing\n",
        ),
        # The values keep their size and times, so only their change time tells.
        (
            {},
            {},
            lambda: _rewrite_keeping_times(
                Path("values.yaml"),
                "name: platen\nstatus: Passing\n",
            ),
            b"# platen\n\nBuild: Passing\n",
        ),
        # An atomic save, so the values' path names a new file.
        (
            {},
            {},
            lambda: _replace(Path("values.yaml"), "name: other\nstatus: passing\n"),
            b"# other\n\nBuild: passing\n",
        ),
        # Only the template's permissions change, and the output must follow them.
        (
            {},
            {},
            lambda: Path("README.template").chmod(0o600),
            b"# platen\n\nBuild: passing\n",
        ),
        # `_badge.txt` is a symlink, replaced by one that leads somewhere else.
        (
            {"_failing.txt": "Build: failing\n", "_passing.txt": "Build: passing\n"},
            {"_badge.new": "_failing.txt", "_badge.txt": "_passing.txt"},
            lambda: os.replace("_badge.new", "_badge.txt"),
            b"# platen\n\nBuild: failing\n",
        ),
        # The include is missing, so `ignore missing` skips it until it's created.
        (
            {
                "README.template": (
                    '# {{ name }}\n{% include "_new.txt" ignore missing %}'
                ),
            },
            {},
            lambda: Path("_new.txt").write_text("New\n", encoding="utf-8"),
            b"# platen\nNew\n",
        ),
        # `_new.txt` is missing, so `_badge.txt` is included instead, until `_new.txt`
        # is created.
        (
            {
                "README.template": (
                    '# {{ name }}\n{% include ["_new.txt", "_badge.txt"] %}'
                ),
            },
            {},
            lambda: Path("_new.txt").write_text("New\n", encoding="utf-8"),
            b"# platen\nNew\n",
        ),
    ],
)
def test_watch_presses_again_when_file_changes(
    files: dict[str, str],
    links: dict[str, str],
    change: Callable[[], object],
    expect: bytes,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press a template again when it, the values, or a template
    that it includes changes, however it changes: in place, even at the same size or
    with its times put back, by being replaced, in its permissions, which the output
    follows, or through a symlink retargeted, or when a template that it tried to
    include is created.
    """
    _create(project, files, links)
    _watching(monkeypatch, change)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    output = project / "README.md"
    result = output.read_bytes()

    assert result == expect

    mode = S_IMODE((project / "README.template").stat().st_mode)
    result = S_IMODE(output.stat().st_mode)

    assert result == mode


@mark.parametrize(
    ("template", "values", "output", "hide", "restore", "message"),
    [
        (
            "README.template",
            "values.yaml",
            "build/README.md",
            lambda: os.rename("README.template", "README.away"),
            lambda: os.rename("README.away", "README.template"),
            "No such file or directory: 'README.template'",
        ),
        (
            "README.template",
            "values.yaml",
            "build/README.md",
            lambda: os.rename("values.yaml", "values.away"),
            lambda: os.rename("values.away", "values.yaml"),
            "No such file or directory: 'values.yaml'",
        ),
        (
            "README.template",
            "conf/values.yaml",
            "build/README.md",
            # The values' directory doesn't exist yet, and `os.renames` creates it.
            lambda: None,
            lambda: os.renames("values.yaml", "conf/values.yaml"),
            "No such file or directory: 'conf/values.yaml'",
        ),
        (
            "templates",
            "values.yaml",
            "build",
            lambda: os.rename("templates", "templates.away"),
            lambda: os.rename("templates.away", "templates"),
            "No such file or directory: 'templates'",
        ),
        # `.platenignore` is a broken symlink until what it leads to is created.
        (
            "templates",
            "values.yaml",
            "build",
            lambda: Path("templates/.platenignore").symlink_to("../platenignore"),
            lambda: Path("platenignore").write_text("_*\n", encoding="utf-8"),
            "No such file or directory: '{templates}/.platenignore'",
        ),
    ],
)
def test_watch_presses_again_when_missing_input_returns(
    template: str,
    values: str,
    output: str,
    hide: Callable[[], object],
    restore: Callable[[], object],
    message: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again once a template, directory of templates, values
    file or `.platenignore` file that was missing returns, even into a directory that
    was missing too.
    """
    templates = project / "templates"

    _create(
        project,
        {
            "templates/README.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            "templates/_badge.txt": "Build: {{ status }}\n",
        },
    )

    hide()
    _watching(monkeypatch, restore)

    exit_code = main(
        [
            "--template",
            template,
            "--values",
            values,
            "--out",
            output,
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: error: {message.format(templates=realpath(templates))}\n"
        f"{WATCHING}"
        f"platen: Pressed '{template}' to '{output}'\n"
    )

    pressed = (project / "build" / "README.md").read_bytes()
    assert pressed == b"# platen\n\nBuild: passing\n"


def test_watch_presses_again_with_real_watcher(project: Path) -> None:
    """
    `platen --watch` must press again as soon as watchfiles sees a change, rather than
    at its next timeout, and press nothing more until something else changes, then
    stop quietly with exit code 130 on SIGINT, like from Ctrl+C.
    """
    with Popen(
        [
            sys.executable,
            "-c",
            # A process that's started in the background inherits SIGINT ignored, and
            # Python then never raises `KeyboardInterrupt`.
            "import signal, sys; "
            "signal.signal(signal.SIGINT, signal.default_int_handler); "
            "from platen.cli import main; "
            "sys.exit(main())",
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
        cwd=project,
        # watchfiles' environment variables can change how it watches.
        env={k: v for k, v in os.environ.items() if not k.startswith("WATCHFILES_")},
        stderr=PIPE,
        stdout=PIPE,
        text=True,
    ) as process:
        assert process.stderr is not None
        assert process.stdout is not None

        stderr = process.stderr
        lines: Queue[str] = Queue()

        def read() -> None:
            for line in stderr:
                lines.put(line)

        reader = Thread(daemon=True, target=read)
        reader.start()

        try:
            result = lines.get(timeout=10)

            assert result == PRESSED_FILE

            result = lines.get(timeout=10)

            assert result == WATCHING

            # Give watchfiles time to start, so that it sees the change. It wakes Platen
            # 5 seconds after it starts without a change, so only seeing the change can
            # press this soon. The time to start and the time to press must stay within
            # those 5 seconds, so give more to starting, which a busy machine can slow,
            # than to pressing, which starts about 50 milliseconds after one change.
            sleep(2)
            _replace(project / "values.yaml", "name: first\nstatus: passing\n")
            result = lines.get(timeout=2)

            assert result == PRESSED_FILE

            # A new watcher starts after every press. Give it time to start too, so
            # that only it can see the next change, rather than Platen's look before
            # it starts.
            sleep(2)
            _replace(project / "values.yaml", "name: second\nstatus: passing\n")
            result = lines.get(timeout=2)

            assert result == PRESSED_FILE

            # Nothing else changes, so nothing more is pressed, however Platen wakes.
            with raises(Empty):
                lines.get(timeout=1)

            process.send_signal(SIGINT)
            exit_code = process.wait(timeout=10)
        finally:
            process.kill()
            process.wait()
            reader.join(timeout=10)

        out = process.stdout.read()

    assert exit_code == 130
    assert out == ""

    # Nothing more was printed, like a traceback.
    assert lines.empty()

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# second\n\nBuild: passing\n"


@mark.parametrize(
    ("template", "output", "watched"),
    [
        ("README.template", "build/README.md", ["."]),
        ("templates", "build", [".", "templates"]),
    ],
)
def test_watch_presses_once_when_nothing_changes(
    template: str,
    output: str,
    watched: list[str],
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press once, then watch every directory that holds what the
    press read, non-recursively, for every event, refusing any directory that it can't
    watch, waking at timeouts, raising Ctrl+C, and with timings and output that
    watchfiles' environment variables can't change, and must not press again when it's
    woken without a change.
    """
    _create(
        project,
        {
            "templates/README.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            "templates/_badge.txt": "Build: {{ status }}\n",
        },
    )

    # Woken without a change, like by watchfiles' timeout.
    started = _watching(monkeypatch, lambda: None)

    exit_code = main(
        [
            "--template",
            template,
            "--values",
            "values.yaml",
            "--out",
            output,
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"platen: Pressed '{template}' to '{output}'\n{WATCHING}"

    expect = [
        (
            tuple(realpath(project / name) for name in watched),
            {
                "debounce": 1_600,
                "debug": False,
                "ignore_permission_denied": False,
                "raise_interrupt": True,
                "recursive": False,
                "rust_timeout": 5_000,
                "step": 50,
                "watch_filter": None,
                "yield_on_timeout": True,
            },
        ),
    ]

    assert started == expect

    pressed = (project / "build" / "README.md").read_bytes()
    assert pressed == b"# platen\n\nBuild: passing\n"


def test_watch_restarts_after_every_press(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must start watching again after every press, even in the same
    directories, because the change that caused the press could have replaced a watched
    directory with another at the same path, which Linux stops watching.
    """
    started = _watching(
        monkeypatch,
        lambda: edit(project / "values.yaml", "name: plated\nstatus: passing\n"),
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root,),
        (root,),
    ]

    result = (project / "README.md").read_bytes()

    assert result == b"# plated\n\nBuild: passing\n"


def test_watch_restarts_when_directories_change(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must start watching again, in every directory that holds what the
    press read, when a press reads from a directory that wasn't watched, like a new
    include's.
    """

    def include() -> None:
        _create(project, {"parts/_part.txt": "Part\n"})
        edit(
            project / "README.template",
            '# {{ name }}\n{% include "parts/_part.txt" %}',
        )

    started = _watching(monkeypatch, include)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root,),
        (root, f"{root}/parts"),
    ]

    result = (project / "README.md").read_bytes()

    assert result == b"# platen\nPart\n"


def test_watch_restarts_when_directory_is_created_within_templates_directory(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must start watching a directory that's created within the
    templates directory, without pressing again, so that a template created in it later
    is seen at once.
    """
    _create(project, {"templates/index.md": "# {{ name }}\n"})

    new = project / "templates" / "new"

    def create() -> set[tuple[Change, str]]:
        new.mkdir()
        return {(Change.added, realpath(new))}

    started = _watching(monkeypatch, create)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_DIRECTORY}{WATCHING}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root, f"{root}/templates"),
        (root, f"{root}/templates", f"{root}/templates/new"),
    ]


@mark.parametrize(
    "change",
    [
        Change.deleted,
        Change.added,
    ],
)
def test_watch_restarts_when_watched_directory_is_replaced(
    change: Change,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must start watching again, without pressing again, when an event
    says that a watched directory was deleted or added, like when a checkout replaces
    it with another at the same path, which Linux stops watching, even though nothing
    that the press read has changed.
    """
    _create(
        project,
        {
            "README.template": (
                '# {{ name }}\n{% include "parts/_part.txt" ignore missing %}'
            ),
        },
    )

    parts = project / "parts"
    parts.mkdir()

    def replace() -> set[tuple[Change, str]]:
        parts.rmdir()
        parts.mkdir()
        return {(change, realpath(parts))}

    started = _watching(monkeypatch, replace)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root, f"{root}/parts"),
        (root, f"{root}/parts"),
    ]


def test_watch_starts_watching_again_after_internal_error(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must start a new watcher when watchfiles raises an internal error,
    like for an event about a file whose name isn't UTF-8 on Linux, which watchfiles
    then raises at every wake, and keep watching, even when the new watcher fails too,
    before it wakes. Then, it must wait 5 seconds before starting another, so that an
    error that every new watcher raises can't start them as fast as they fail, and warn
    once that it's looking for changes that often instead, until a watcher wakes.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("platen.cli.sleep", sleeps.append)

    error = 'Unable to decode path "/docs/caf\\xe9" to string'

    def fail() -> None:
        raise RuntimeError(error)

    started = _watching(
        monkeypatch,
        fail,
        lambda: None,
        fail,
        fail,
        fail,
        lambda: None,
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"{PRESSED_FILE}{WATCHING}"
        "platen: warning: Can't watch for changes, so looking for them every 5 seconds "
        f"instead: {error}\n"
    )

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root,),
        (root,),
        (root,),
        (root,),
        (root,),
    ]

    # The third and fourth watchers each replaced one that failed, and failed too before
    # they woke. The first failed before it woke, but replaced none.
    assert sleeps == [5, 5]


def test_watch_starts_watching_again_after_internal_error_without_waiting_after_press(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must not wait before starting a new watcher when one fails before
    it wakes, if the watcher that it replaced ended for a press rather than failing,
    even though the one before that failed.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("platen.cli.sleep", sleeps.append)

    def fail() -> None:
        raise RuntimeError('Unable to decode path "/docs/caf\\xe9" to string')

    def change_then_fail() -> None:
        edit(project / "values.yaml", "name: other\nstatus: passing\n")
        fail()

    started = _watching(
        monkeypatch,
        change_then_fail,
        # The watcher that replaced the failed one presses before it wakes, so this
        # fails the watcher after that one.
        fail,
        lambda: None,
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    root = realpath(project)
    result = [paths for paths, _ in started]

    # The watcher that pressed never woke, so it never started.
    assert result == [
        (root,),
        (root,),
        (root,),
    ]

    assert sleeps == []


@mark.parametrize(
    ("interrupted", "changes", "err", "expect"),
    [
        # The interrupt comes during the first press, before anything's watched.
        (1, [], "", None),
        # Nothing changes, so the interrupt comes while waiting for a change.
        (2, [], f"{PRESSED_FILE}{WATCHING}", b"# platen\n\nBuild: passing\n"),
        # The values change, so the interrupt comes during the press that follows.
        (
            2,
            [lambda: edit(Path("values.yaml"), "name: other\nstatus: passing\n")],
            f"{PRESSED_FILE}{WATCHING}",
            b"# platen\n\nBuild: passing\n",
        ),
    ],
)
def test_watch_stops_quietly_when_interrupted(
    interrupted: int,
    changes: list[Callable[[], object]],
    err: str,
    expect: bytes | None,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    An interrupt must stop `platen --watch` quietly, with exit code 130, whether it
    comes during the first press, before anything's watched, while waiting for a
    change, or during a later press.
    """
    press_file = Platen.press_file
    presses = count(1)

    def press_file_unless_interrupted(
        self: Platen,
        template: PathLike[str] | str,
        destination: PathLike[str] | str,
    ) -> None:
        if next(presses) == interrupted:
            raise KeyboardInterrupt

        press_file(self, template, destination)

    monkeypatch.setattr("platen.cli.Platen.press_file", press_file_unless_interrupted)
    _watching(monkeypatch, *changes)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == err

    output = project / "README.md"
    result = output.read_bytes() if output.exists() else None

    assert result == expect


def test_watch_stops_watching_template_that_is_no_longer_included(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must stop watching a template that the last press no longer read,
    like an include that's been removed.
    """
    _watching(
        monkeypatch,
        lambda: edit(project / "README.template", "# {{ name }}\n"),
        lambda: edit(project / "_badge.txt", "Build: {{ status }}!\n"),
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_FILE}{WATCHING}{PRESSED_FILE}"

    result = (project / "README.md").read_bytes()

    assert result == b"# platen\n"


def test_watch_warns_when_directory_has_nothing_to_press(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must warn when a directory has nothing to press, as a press without
    `--watch` does, and press once a template is created there.
    """
    templates = project / "templates"
    templates.mkdir()

    _watching(
        monkeypatch,
        lambda: (templates / "index.md").write_text("# {{ name }}\n", encoding="utf-8"),
    )

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: warning: Nothing to press in 'templates': it's empty, or everything "
        "in it is ignored\n"
        f"{WATCHING}{PRESSED_DIRECTORY}"
    )

    result = (project / "build" / "index.md").read_bytes()

    assert result == b"# platen\n"


def test_watch_watches_directories_of_everything_read(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must watch the real path to each directory that holds something
    that the press read, and to each directory that a symlink among them leads into,
    and no other, not even the working directory.
    """
    _create(
        project,
        {
            "conf/values.yaml": "name: platen\n",
            "docs/page.template": (
                '{% include "parts/_part.txt" %}{% include "_link.txt" %}'
            ),
            "docs/parts/_part.txt": "Part\n",
            "shared/_shared.txt": "Shared\n",
        },
        {"docs/_link.txt": "../shared/_shared.txt"},
    )

    started = _watching(monkeypatch)

    exit_code = main(
        [
            "--template",
            "docs/page.template",
            "--values",
            "conf/values.yaml",
            "--out",
            "build/page.md",
            "--watch",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: Pressed 'docs/page.template' to 'build/page.md'\n{WATCHING}"
    )

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (
            f"{root}/conf",
            f"{root}/docs",
            f"{root}/docs/parts",
            f"{root}/shared",
        ),
    ]

    result = (project / "build" / "page.md").read_bytes()

    assert result == b"Part\nShared\n"
