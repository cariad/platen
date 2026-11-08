import os
from collections.abc import Callable
from itertools import count
from os import PathLike, link
from os.path import realpath
from pathlib import Path
from shutil import copymode
from stat import S_IMODE

from pytest import CaptureFixture, MonkeyPatch, mark, skip
from rootle.testing import (
    can_chmod_deny_search,
    edit,
    rewrite_and_keep_times,
    save_atomically,
    snapshot,
)
from watchfiles import Change

from platen import Platen
from platen.cli import main
from tests.cli.fakes import watching
from tests.cli.helpers import create_files
from tests.cli.lines import (
    PRESSED_DIRECTORY,
    PRESSED_FILE,
    WATCHING,
)


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

    watching(
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

    watching(monkeypatch, delete)

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
    monkeypatch.setattr("platen.cli.watch.sleep", sleeps.append)

    def fail() -> None:
        raise FileNotFoundError("No such file or directory (os error 2)")

    started = watching(
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

    started = watching(monkeypatch, disappear)

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
    create_files(project, files)
    changed: list[dict[Path, tuple[bytes, int] | None]] = []

    def change_and_snapshot() -> None:
        change()
        changed.append(snapshot(project))

    watching(monkeypatch, change_and_snapshot)

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
    create_files(project, files, links)
    watching(
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
    create_files(
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

    watching(monkeypatch)

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
    create_files(
        project,
        {
            "README.template": (
                '# {{ name }}\n{% include "parts/_part.txt" ignore missing %}'
            ),
        },
    )

    (project / "parts").mkdir()

    started = watching(
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

    create_files(project, files, links)

    for name in hidden:
        (project / name).chmod(0)

    try:
        watching(monkeypatch, fix)

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

    monkeypatch.setattr("platen.Platen.press_file", press_file_then_change_values)

    watching(
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
    create_files(
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

    watching(monkeypatch)

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

    monkeypatch.setattr("platen.Platen.press_file", press_file_then_change_include)

    watching(monkeypatch)

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

    create_files(
        project,
        {
            "templates/.platenignore": "_*\n",
            "templates/_badge.txt": "Build: {{ status }}\n",
            "templates/index.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            **files,
        },
    )

    watching(monkeypatch, change)

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
            lambda: rewrite_and_keep_times(
                Path("values.yaml"),
                "name: platen\nstatus: Passing\n",
            ),
            b"# platen\n\nBuild: Passing\n",
        ),
        # An atomic save, so the values' path names a new file.
        (
            {},
            {},
            lambda: save_atomically(
                Path("values.yaml"), "name: other\nstatus: passing\n"
            ),
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
    create_files(project, files, links)
    watching(monkeypatch, change)

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

    create_files(
        project,
        {
            "templates/README.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            "templates/_badge.txt": "Build: {{ status }}\n",
        },
    )

    hide()
    watching(monkeypatch, restore)

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


def test_watch_presses_again_when_template_symlink_is_retargeted_to_hard_link(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again when a symlink to the template is retargeted to a
    hard link to the same file by another name, even though nothing that it reads has
    changed, because Jinja chooses autoescaping by that name.
    """
    create_files(
        project,
        {
            "body.txt": "{{ name }}\n",
            "values.yaml": 'name: "<b>"\n',
        },
    )

    link(
        project / "body.txt",
        project / "body.html",
    )

    create_files(
        project,
        {},
        {
            "README.template": "body.txt",
        },
    )

    # Retarget the symlink atomically, like `ln -sfn` does.
    watching(
        monkeypatch,
        lambda: (
            create_files(
                project,
                {},
                {
                    "README.new": "body.html",
                },
            ),
            os.replace(
                "README.new",
                "README.template",
            ),
        ),
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
    assert result == b"&lt;b&gt;\n"


def test_watch_presses_again_when_templates_directory_symlink_is_retargeted(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """
    `platen --watch` must press again when a symlink to the template's directory is
    retargeted to a copy made of hard links, even though nothing that it reads has
    changed, and then press again when an include in the copy is replaced, because
    includes are found there.
    """
    create_files(
        project,
        {
            "a/_footer.md": "Footer\n",
            "a/page.md": '{% include "_footer.md" %}',
        },
        {
            "site": "a",
        },
    )

    # A copy made of hard links, like `cp -al` makes.
    (project / "b").mkdir()

    for name in ("_footer.md", "page.md"):
        link(
            project / "a" / name,
            project / "b" / name,
        )

    watching(
        monkeypatch,
        # Retarget the symlink atomically, like `ln -sfn` does.
        lambda: (
            create_files(project, {}, {"site.new": "b"}),
            os.replace("site.new", "site"),
        ),
        lambda: save_atomically(project / "b" / "_footer.md", "New footer\n"),
    )

    exit_code = main(
        [
            "--template",
            "site/page.md",
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

    pressed = "platen: Pressed 'site/page.md' to 'README.md'\n"
    assert captured.err == f"{pressed}{WATCHING}{pressed}{pressed}"

    result = (project / "README.md").read_bytes()
    assert result == b"New footer\n"


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
    create_files(
        project,
        {
            "templates/README.md": '# {{ name }}\n\n{% include "_badge.txt" %}\n',
            "templates/_badge.txt": "Build: {{ status }}\n",
        },
    )

    # Woken without a change, like by watchfiles' timeout.
    started = watching(monkeypatch, lambda: None)

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
    started = watching(
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
        create_files(project, {"parts/_part.txt": "Part\n"})
        edit(
            project / "README.template",
            '# {{ name }}\n{% include "parts/_part.txt" %}',
        )

    started = watching(monkeypatch, include)

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
    create_files(project, {"templates/index.md": "# {{ name }}\n"})

    new = project / "templates" / "new"

    def create() -> set[tuple[Change, str]]:
        new.mkdir()
        return {(Change.added, realpath(new))}

    started = watching(monkeypatch, create)

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
    create_files(
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

    started = watching(monkeypatch, replace)

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
    monkeypatch.setattr("platen.cli.watch.sleep", sleeps.append)

    error = 'Unable to decode path "/docs/caf\\xe9" to string'

    def fail() -> None:
        raise RuntimeError(error)

    started = watching(
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
    monkeypatch.setattr("platen.cli.watch.sleep", sleeps.append)

    def fail() -> None:
        raise RuntimeError('Unable to decode path "/docs/caf\\xe9" to string')

    def change_then_fail() -> None:
        edit(project / "values.yaml", "name: other\nstatus: passing\n")
        fail()

    started = watching(
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

    monkeypatch.setattr("platen.Platen.press_file", press_file_unless_interrupted)
    watching(monkeypatch, *changes)

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
    watching(
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
    create_files(
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

    started = watching(monkeypatch)

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
