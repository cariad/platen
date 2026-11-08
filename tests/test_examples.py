from pathlib import Path

from rootle.testing import snapshot

from platen import Platen
from platen.cli.values import load_values

_examples_dir = Path(__file__).parent.parent / "examples"


def test_task_list(tmp_path: Path) -> None:
    """
    Pressing the task list example must reproduce its committed build exactly, so that
    a change to pressing can't leave the example out of date.
    """
    example = _examples_dir / "task_list"
    values = load_values(example / "tasks.yaml")

    Platen(example / "templates", values).press(tmp_path)

    result = snapshot(tmp_path)
    expect = snapshot(example / "build")

    assert result == expect
