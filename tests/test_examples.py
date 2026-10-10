from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML

from platen import Platen
from tests.snapshots import snapshot

_examples_dir = Path(__file__).parent.parent / "examples"


def test_task_list(tmp_path: Path) -> None:
    """
    Pressing the task list example must reproduce its committed build exactly, so that
    a change to pressing can't leave the example out of date.
    """
    example = _examples_dir / "task_list"
    values = YAML(typ="safe").load(example / "tasks.yaml")  # pyright: ignore[reportUnknownMemberType]
    assert isinstance(values, Mapping)

    Platen(example / "templates", cast(Mapping[str, Any], values)).press(tmp_path)

    assert snapshot(tmp_path) == snapshot(example / "build")
