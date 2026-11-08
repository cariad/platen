from collections.abc import Callable
from errno import EACCES, EADDRINUSE
from itertools import count
from os import PathLike, strerror
from os.path import realpath
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch, mark
from rootle.testing import edit

from platen import Platen
from platen.cli import main
from tests.cli.fakes import FakeServer, serving, watching
from tests.cli.helpers import assert_fails, create_files
from tests.cli.lines import (
    PRESSED_DIRECTORY,
    PRESSED_FILE,
    RELOADED,
    SERVING_DIRECTORY,
    SERVING_FILE,
    WATCHING,
)


def test_serve_fails_when_output_directory_is_file(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must stop with exit code 1, before pressing or watching anything,
    when the directory that it would serve is a file, naming the file as it was given.
    """
    create_files(
        project,
        {
            "build": "Not a directory\n",
            "templates/index.md": "# {{ name }}\n",
        },
    )

    # The real server fails before it claims its port. Should it get that far, port 0
    # makes it fail, rather than claim the port that Platen might be serving on.
    monkeypatch.setattr("platen.cli.serve.PORT", 0)
    started = watching(monkeypatch)

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
            "--serve",
        ],
        "Not a directory: 'build'",
    )

    assert started == []


@mark.parametrize(
    "number",
    [
        EADDRINUSE,
        # Like for a port that this user isn't allowed to serve on.
        EACCES,
    ],
)
def test_serve_fails_when_port_cannot_be_claimed(
    number: int,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must stop with exit code 1, before pressing or watching anything,
    when it can't claim the port, like when it's already in use, describing the error
    without its number.
    """
    serving(monkeypatch, OSError(number, strerror(number)))
    started = watching(monkeypatch)

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
            "--serve",
        ],
        f"Can't serve on port 1440: {strerror(number)}",
    )

    assert started == []


def test_serve_fails_when_working_directory_is_deleted(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must stop with exit code 1, before pressing or watching anything,
    when the working directory has been deleted, because the relative directory that
    it would serve leads nowhere.
    """
    # The real server fails before it claims its port. Should it get that far, port 0
    # makes it fail, rather than claim the port that Platen might be serving on.
    monkeypatch.setattr("platen.cli.serve.PORT", 0)
    started = watching(monkeypatch)

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
            "--serve",
        ],
        "The working directory no longer exists",
    )

    assert started == []


def test_serve_leaves_created_directory_when_port_cannot_be_claimed(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must create the directory that it serves when it's missing, since
    the server serves only one that exists, and leave it when it then can't claim the
    port, having pressed nothing.
    """
    create_files(project, {"templates/index.md": "# {{ name }}\n"})
    serving(monkeypatch, OSError(EADDRINUSE, strerror(EADDRINUSE)))
    started = watching(monkeypatch)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "site/build",
            "--serve",
        ],
    )

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: error: Can't serve on port 1440: {strerror(EADDRINUSE)}\n"
    )

    result = list((project / "site" / "build").iterdir())

    assert result == []
    assert started == []


def test_serve_reloads_after_every_press(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must reload every page after every press, once the press has
    printed its line, whether it succeeded or failed, and say what it's serving after
    the first press, before it says that it's watching. It must print nothing on
    stdout.
    """
    servers = serving(monkeypatch)

    watching(
        monkeypatch,
        lambda: edit(project / "values.yaml", "name: second\nstatus: passing\n"),
        lambda: edit(project / "_badge.txt", "Build: {{ missing }}\n"),
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
            "--serve",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    root = realpath(project)

    assert captured.err == (
        f"{PRESSED_FILE}{RELOADED}{SERVING_FILE}{WATCHING}"
        f"{PRESSED_FILE}{RELOADED}"
        f"platen: error: 'missing' is undefined ({root}/_badge.txt, line 1)\n"
        f"{RELOADED}"
        f"{PRESSED_FILE}{RELOADED}"
    )

    expect = [
        FakeServer(".", 1440, entered=True, exited=True, reloads=4),
    ]

    assert servers == expect

    result = (project / "README.md").read_bytes()

    assert result == b"# second\n\nBuild: passing!\n"


def test_serve_reloads_when_directory_has_nothing_to_press(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must reload every page after a press that warns that a directory
    has nothing to press, as well as after a press that presses.
    """
    templates = project / "templates"
    templates.mkdir()
    servers = serving(monkeypatch)

    watching(
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
            "--serve",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: warning: Nothing to press in 'templates': it's empty, or everything "
        "in it is ignored\n"
        f"{RELOADED}{SERVING_DIRECTORY}{WATCHING}{PRESSED_DIRECTORY}{RELOADED}"
    )

    expect = [
        FakeServer("build", 1440, entered=True, exited=True, reloads=2),
    ]

    assert servers == expect


@mark.parametrize(
    ("output", "directory", "url"),
    [
        # An output in the working directory serves the working directory.
        ("README.md", ".", "http://localhost:1440/README.md"),
        # The directory doesn't exist until Platen creates it to serve it.
        ("build/README.md", "build", "http://localhost:1440/README.md"),
        # A press ignores a trailing separator, so this is still a file.
        ("build/README.md/", "build", "http://localhost:1440/README.md"),
    ],
)
def test_serve_serves_directory_of_output_file(
    output: str,
    directory: str,
    url: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must serve the directory that a template that's a file is pressed
    into, so that the file's links to the files beside it work, on localhost port 1440,
    and print the URL of the file, naming the output as it was given.
    """
    servers = serving(monkeypatch)
    watching(monkeypatch)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            output,
            "--serve",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: Pressed 'README.template' to '{output}'\n"
        f"{RELOADED}"
        f"platen: Serving '{output}' at {url}\n"
        f"{WATCHING}"
    )

    expect = [
        FakeServer(directory, 1440, entered=True, exited=True, reloads=1),
    ]

    assert servers == expect

    result = (project / output).read_bytes()

    assert result == b"# platen\n\nBuild: passing\n"


def test_serve_serves_directory_of_output_when_template_is_missing(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must serve the directory that the output is in, and print the URL
    of the output, like for a template that's a file, when the template doesn't exist
    when it starts, and reload after the first press fails. It must keep serving that
    directory once the template appears, even as a directory.
    """
    servers = serving(monkeypatch)

    watching(
        monkeypatch,
        lambda: create_files(project, {"templates/index.md": "# {{ name }}\n"}),
    )

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            "--serve",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        "platen: error: No such file or directory: 'templates'\n"
        f"{RELOADED}"
        "platen: Serving 'build' at http://localhost:1440/build\n"
        f"{WATCHING}{PRESSED_DIRECTORY}{RELOADED}"
    )

    expect = [
        FakeServer(".", 1440, entered=True, exited=True, reloads=2),
    ]

    assert servers == expect

    result = (project / "build" / "index.md").read_bytes()

    assert result == b"# platen\n"


@mark.parametrize(
    "flags",
    [
        [
            "--serve",
        ],
        # `--serve` watches like `--watch` does, so both together are the same.
        [
            "--serve",
            "--watch",
        ],
        [
            "--watch",
            "--serve",
        ],
    ],
)
def test_serve_serves_output_directory(
    flags: list[str],
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --serve` must serve the directory that a directory of templates is pressed
    into, on localhost port 1440, and watch like `platen --watch` does, with only one
    server and one watcher when it's given `--watch` too.
    """
    create_files(project, {"templates/index.md": "# {{ name }}\n"})
    servers = serving(monkeypatch)
    started = watching(monkeypatch)

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "values.yaml",
            "--out",
            "build",
            *flags,
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{PRESSED_DIRECTORY}{RELOADED}{SERVING_DIRECTORY}{WATCHING}"

    expect = [
        FakeServer("build", 1440, entered=True, exited=True, reloads=1),
    ]

    assert servers == expect

    root = realpath(project)
    result = [paths for paths, _ in started]

    assert result == [
        (root, f"{root}/templates"),
    ]

    result = (project / "build" / "index.md").read_bytes()

    assert result == b"# platen\n"


@mark.parametrize(
    ("interrupted", "changes", "err", "reloads"),
    [
        # The interrupt comes during the first press, before anything's watched. The
        # press never ends, so nothing reloads.
        (1, [], "", 0),
        # Nothing changes, so the interrupt comes while waiting for a change.
        (2, [], f"{PRESSED_FILE}{RELOADED}{SERVING_FILE}{WATCHING}", 1),
        # The values change, so the interrupt comes during the press that follows.
        (
            2,
            [lambda: edit(Path("values.yaml"), "name: other\nstatus: passing\n")],
            f"{PRESSED_FILE}{RELOADED}{SERVING_FILE}{WATCHING}",
            1,
        ),
    ],
)
def test_serve_stops_quietly_when_interrupted(
    interrupted: int,
    changes: list[Callable[[], object]],
    err: str,
    reloads: int,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    An interrupt must stop `platen --serve` quietly, with exit code 130, and stop the
    server, whether it comes during the first press, while waiting for a change, or
    during a later press.
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

    monkeypatch.setattr("platen.Platen.press_file", press_file_unless_interrupted)
    servers = serving(monkeypatch)
    watching(monkeypatch, *changes)

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--serve",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == err

    expect = [
        FakeServer(".", 1440, entered=True, exited=True, reloads=reloads),
    ]

    assert servers == expect
