from logging import NullHandler, getLogger

from .exceptions import (
    DestinationIsTemplateError,
    DestinationWithinDirectoryError,
    PlatenError,
    TemplateNotInDirectoryError,
)
from .platen import Platen

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "DestinationIsTemplateError",
    "DestinationWithinDirectoryError",
    "Platen",
    "PlatenError",
    "TemplateNotInDirectoryError",
]
