from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from stat import S_ISDIR

from ..platen import Platen
from .status import status
from .types import Look


@lru_cache(maxsize=1)
def _platen(templates_dir: Path) -> Platen:
    """
    Get a `Platen` to walk the templates directory at `templates_dir` with.

    A watch looks at the same directory again and again, and a walk never changes the
    `Platen`, so keep the last one rather than build a Jinja environment every time.

    Args:
        templates_dir: Resolved path to the templates directory, so that a symlink
            that's retargeted gets a new `Platen`.

    Returns:
        The `Platen`.
    """
    return Platen(templates_dir, {})


def look(template: str, values_path: str, read: Iterable[str]) -> Look:
    """
    Look at everything that a press of `template` depends on.

    A walk finds the templates that a press would press, but not the templates that
    they reference, because a reference can be named from the values. Only a press can
    find those, so pass the paths that the last press read as `read`.

    Args:
        template: Path to the template, or directory of templates, as it was given.
        values_path: Path to the values file, as it was given.
        read: Paths to the templates that the last press read, except any that it
            wrote to.

    Returns:
        The look.
    """
    paths = [template, values_path, *read]
    names: tuple[str, ...] | None = None

    if (found := status(template)) is not None and S_ISDIR(found.mode):
        platen = _platen(Path(template).resolve())

        try:
            names = tuple(platen.templates())
        except OSError as error:
            # Watch what stopped the walk, like a directory that can't be read or a
            # broken `.platenignore` symlink, so that the fix is seen. The press
            # reports the error itself.
            paths.append(str(error.filename or template))
        else:
            paths.extend(str(platen.templates_dir / name) for name in names)

    return Look(names, {path: status(path) for path in paths})
