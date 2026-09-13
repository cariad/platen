from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pytest import FixtureRequest, fixture
from ruamel.yaml import YAML

from platen.platen import Platen
from tests.types import AssertFileWasPressed

_yaml = YAML(typ="safe")


@fixture
def assert_file_was_pressed(
    expect_dir: Path,
    platen: Platen,
) -> AssertFileWasPressed:
    """A callback that asserts that a file was pressed."""

    def _assert(actual: Path) -> None:
        assert actual.exists(), f"Nothing pressed at '{actual}'"

        expect = expect_dir / actual.relative_to(platen.output_dir)
        assert expect.exists(), f"No expectation at '{expect}'"

        actual_body = actual.read_text(encoding="utf-8")
        expect_body = expect.read_text(encoding="utf-8")
        assert actual_body == expect_body

    return _assert


@fixture
def expect_dir(request: FixtureRequest) -> Path:
    """Path to the directory of this test's expected result."""
    return Path(request.path.parent / "expect")


@fixture
def platen(
    values: Mapping[str, Any],
    templates_dir: Path,
    output_dir: Path,
) -> Platen:
    """A `Platen` instance that outputs to a temporary directory."""
    return Platen(
        templates_dir,
        output_dir,
        values,
    )


@fixture
def values(request: FixtureRequest) -> Mapping[str, Any]:
    """This test's values to be pressed."""
    path = request.path.parent / "values.yaml"
    data = _yaml.load(path)  # pyright: ignore[reportUnknownMemberType]
    assert isinstance(data, Mapping), f"'{path}' must be a mapping"
    return cast(Mapping[str, Any], data)
