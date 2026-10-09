from logging import NullHandler, getLogger

from .exceptions import (
    DestinationIsProtectedError,
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    PlatenError,
    TemplateNotInDirectoryError,
    TemplateNotPressableError,
)
from .platen import Platen

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "DestinationIsProtectedError",
    "DestinationIsTemplateError",
    "DestinationWithinDirectoryError",
    "Platen",
    "PlatenError",
    "TemplateNotInDirectoryError",
    "TemplateNotPressableError",
]
