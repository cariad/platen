from collections.abc import Callable
from pathlib import Path

AssertFileWasPressed = Callable[[Path], None]
"""Callback to assert that a file was pressed."""
