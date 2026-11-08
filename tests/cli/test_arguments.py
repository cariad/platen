from importlib.metadata import PackageNotFoundError, entry_points, version
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch, mark, raises
from rootle.testing import snapshot

from platen.cli import main
from tests.cli.lines import (
    USAGE,
)


def test_console_script_runs_main() -> None:
    """The `platen` console script must run `platen.cli.main`."""
    (script,) = entry_points(group="console_scripts", name="platen")
    result = script.load()

    assert result is main


def test_help(capsys: CaptureFixture[str], monkeypatch: MonkeyPatch) -> None:
    """
    `--help` must print usage to stdout, naming both of the template's options,
    `--watch`, and `--serve` with the URL that it serves on, and exit with 0.
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
    assert "\n  --serve " in captured.out
    assert " http://localhost:1440/ " in captured.out
    assert captured.err == ""


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
        (
            [
                "--template",
                "README.template",
                "--values",
                "values.yaml",
                "--out",
                "README.md",
                "--serve=yes",
            ],
            "argument --serve: ignored explicit argument 'yes'",
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
    The wrong number of arguments, or a value given to `--watch` or `--serve`, must
    print usage and an error to stderr, exit with 2, and write nothing.
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

    monkeypatch.setattr("platen.cli.arguments.version", missing)

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
