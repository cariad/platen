from pathlib import Path

from pytest import FixtureRequest, fixture


@fixture
def output_dir(tmp_path: Path) -> Path:
    """Path to this test's build output directory."""
    return tmp_path / "target"


@fixture
def templates_dir(request: FixtureRequest) -> Path:
    """Path to this test's source templates directory."""
    return Path(request.path.parent / "source")
