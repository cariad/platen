from collections.abc import Mapping

from rootle.types import Fingerprint

from .types import Look


def baseline(
    before: Look,
    read: Mapping[str, Fingerprint | None],
    after: Look,
) -> Look:
    """
    Find the look that a press was pressed from.

    Each template that the press read is given its fingerprint from just before the
    press read it, and every other path its fingerprint from before the press, so that
    a change made while the press ran is out of date as soon as the press ends. A path
    that the press didn't read and that couldn't be looked at before, like a template
    that a walk after the press found for the first time, is given its fingerprint
    from after.

    Args:
        before: The look taken just before the press.
        read: The fingerprint of each template that the press read, from just before it
            first read it, keyed by its path.
        after: The look taken just after the press, at every path that it read.

    Returns:
        The look to compare later looks with.
    """
    return Look(
        before.templates_dir,
        before.names,
        {
            path: read[path] if path in read else before.fingerprints.get(path, found)
            for path, found in after.fingerprints.items()
        },
    )
