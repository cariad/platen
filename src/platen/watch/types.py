from typing import NamedTuple

from rootle.types import Fingerprint


class Look(NamedTuple):
    """
    What a look saw of everything that a press depends on.

    Compare looks with `==`. A press is out of date when a look differs from the look
    that it was pressed from.
    """

    templates_dir: str | None
    """
    Real path to the source templates directory that a press would load templates from,
    or `None` when the template can't be found, or can't be resolved.

    It's kept apart from `fingerprints`, because it can change when the fingerprints
    don't:
    retargeting a symlink to the template's directory, so that it leads to a copy of
    hard links, changes where includes are found.
    """

    names: tuple[str, ...] | None
    """
    Every template that a press would press, as Jinja names it within `templates_dir`,
    or `None` when the template can't be found, can't be resolved, leads outside
    `templates_dir`, or a walk failed.

    A directory's names are every template that a walk finds, in walk order. A file's
    name is the file that it leads to, through any symlinks, because Jinja chooses
    autoescaping by that name.

    The names are kept apart from `fingerprints`, because they can change when the
    fingerprints don't: newly ignoring a template that the last press read keeps its
    path watched, and retargeting a symlink to the template, so that it leads to a hard
    link to the same file by another name, keeps every fingerprint the same.
    """

    fingerprints: dict[str, Fingerprint | None]
    """
    The fingerprint of each watched path, keyed by the path as it's watched, or `None`
    when nothing can be found there.
    """
