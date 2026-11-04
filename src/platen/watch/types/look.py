from typing import NamedTuple

from .status import Status


class Look(NamedTuple):
    """
    What a look saw of everything that a press depends on.

    Compare looks with `==`. A press is out of date when a look differs from the look
    that it was pressed from.
    """

    names: tuple[str, ...] | None
    """
    Every template that a walk of the templates directory found, in walk order, or
    `None` when the template isn't a directory or the walk failed.

    The names are kept apart from `statuses`, because they can change when the statuses
    don't: newly ignoring a template that the last press read keeps its path watched.
    """

    statuses: dict[str, Status | None]
    """
    The status of each watched path, keyed by the path as it's watched, or `None` when
    nothing can be found there.
    """
