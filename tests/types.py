from collections.abc import Callable
from pathlib import Path

AssertFileWasPressed = Callable[[Path, Path], None]
"""Callback to assert that a template was pressed to a file."""

AssertOutputMatchesExpect = Callable[[], None]
"""Callback to assert that the output directory matches expectations."""
