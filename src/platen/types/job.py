from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Job:
    """
    A template to press, and where to press it.

    The destination is resolved once, when the job is planned, so that the checks before
    a press and the write itself always agree on where the result goes.

    Attributes:
        destination: Path to the file to write, as it was given.
        name: POSIX-style path to the template, relative to the source templates
            directory. This is also the template's name in Jinja.
        parent: The destination's parent, resolved with `realpath`. This is the
            directory that's created, and that the result is written into.
        resolved: The destination, resolved with `realpath`. This is where the result
            goes, even when the destination is a symlink.
    """

    destination: Path
    """Path to the file to write, as it was given."""

    name: str
    """
    POSIX-style path to the template, relative to the source templates directory. This
    is also the template's name in Jinja.
    """

    parent: Path
    """
    The destination's parent, resolved with `realpath`. This is the directory that's
    created, and that the result is written into.
    """

    resolved: Path
    """
    The destination, resolved with `realpath`. This is where the result goes, even when
    the destination is a symlink.
    """
