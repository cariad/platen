from pathlib import Path

from pytest import FixtureRequest, MonkeyPatch, fixture


@fixture
def output_dir(tmp_path: Path) -> Path:
    """Path to this test's build output directory."""
    return tmp_path / "target"


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


@fixture
def templates_dir(request: FixtureRequest) -> Path:
    """Path to this test's source templates directory."""
    return Path(request.path.parent / "source")
