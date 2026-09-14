from logging import NullHandler, getLogger

from .exceptions import (
    NestedDirectoriesError,
    PlatenError,
    TemplateNotInDirectoryError,
)
from .platen import Platen

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "NestedDirectoriesError",
    "Platen",
    "PlatenError",
    "TemplateNotInDirectoryError",
]
