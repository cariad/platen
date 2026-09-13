from logging import NullHandler, getLogger

from .exceptions import PlatenError, TemplateNotInDirectoryError
from .platen import Platen

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "Platen",
    "PlatenError",
    "TemplateNotInDirectoryError",
]
