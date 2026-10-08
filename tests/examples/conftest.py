from collections.abc import Mapping
from pathlib import Path
from stat import S_IMODE
from typing import Any, cast

from pytest import FixtureRequest, fixture
from ruamel.yaml import YAML

from platen.platen import Platen
from tests.types import AssertFileWasPressed, AssertOutputMatchesExpect

_yaml = YAML(typ="safe")


def _relative_files(directory: Path) -> set[Path]:
    """Relative paths of every file within a directory."""
    return {
        path.relative_to(directory) for path in directory.rglob("*") if path.is_file()
    }


@fixture
def assert_file_was_pressed(
    expect_dir: Path,
    output_dir: Path,
) -> AssertFileWasPressed:
    """A callback that asserts that a template was pressed to a file."""

    def _assert(template: Path, actual: Path) -> None:
        assert actual.exists(), f"Nothing pressed at '{actual}'"

        expect = expect_dir / actual.relative_to(output_dir)
        assert expect.exists(), f"No expectation at '{expect}'"

        actual_body = actual.read_bytes()
        expect_body = expect.read_bytes()
        assert actual_body == expect_body

        actual_mode = S_IMODE(actual.stat().st_mode)
        expect_mode = S_IMODE(template.stat().st_mode)

        assert actual_mode == expect_mode, (
            f"'{actual}' has mode {actual_mode:o} but its template "
            f"'{template}' has mode {expect_mode:o}"
        )

    return _assert


@fixture
def assert_output_matches_expect(
    assert_file_was_pressed: AssertFileWasPressed,
    expect_dir: Path,
    output_dir: Path,
    templates_dir: Path,
) -> AssertOutputMatchesExpect:
    """
    A callback that asserts that the output directory contains exactly
    the expected files, and that each of them was pressed from the
    template at the same relative path.
    """

    def _assert() -> None:
        expect = _relative_files(expect_dir)
        actual = _relative_files(output_dir)
        assert actual == expect

        for rel_path in actual:
            assert_file_was_pressed(
                templates_dir / rel_path,
                output_dir / rel_path,
            )

    return _assert


@fixture
def expect_dir(request: FixtureRequest) -> Path:
    """Path to the directory of this test's expected result."""
    return Path(request.path.parent / "expect")


@fixture
def platen(
    values: Mapping[str, Any],
    templates_dir: Path,
) -> Platen:
    """A `Platen` instance that presses this test's templates and values."""
    return Platen(
        templates_dir,
        values,
    )


@fixture
def values(request: FixtureRequest) -> Mapping[str, Any]:
    """This test's values to be pressed."""
    path = request.path.parent / "values.yaml"
    data = _yaml.load(path)  # pyright: ignore[reportUnknownMemberType]
    assert isinstance(data, Mapping), f"'{path}' must be a mapping"
    return cast(Mapping[str, Any], data)
