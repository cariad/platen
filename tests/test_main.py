import sys
from pathlib import Path
from runpy import run_module

from pytest import MonkeyPatch, raises


def test_module_runs_main(monkeypatch: MonkeyPatch, project: Path) -> None:
    """`python -m platen` must run `platen.cli.main`, and exit with its code."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "platen",
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    with raises(SystemExit) as ex:
        run_module("platen", run_name="__main__")

    exit_code = ex.value.code

    assert exit_code == 0

    result = (project / "README.md").read_bytes()

    assert result == b"# platen\n\nBuild: passing\n"
