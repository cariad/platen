from collections.abc import Iterable
from functools import lru_cache
from os.path import split
from pathlib import Path
from stat import S_ISDIR

from rootle import relative_within

from ..platen import Platen
from .status import status
from .types import Look


def _name(template: str) -> tuple[str | None, str | None]:
    """
    Name a template that isn't a directory, the way a press of it would.

    A press loads templates from the template's own directory, resolved, and names the
    template by the file that it leads to, through any symlinks, within that directory.

    Args:
        template: Path to the template, as it was given.

    Returns:
        The real path to the source templates directory, and the template's name within
        it. The name is `None` when the template leads outside the directory, and both
        are `None` when either can't be resolved, like when a part of the path can't be
        read. A press fails in each of those cases.
    """
    # `platen.cli` splits the template like this to find its templates directory.
    directory, name = split(template)

    try:
        templates_dir = Path(directory or ".").resolve()

        # Like `Platen.press_file` names it, which can't be asked without pressing.
        relative = relative_within(templates_dir / name, templates_dir)

    except OSError:
        return None, None

    return str(templates_dir), None if relative is None else relative.as_posix()


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
    templates_dir: str | None = None
    names: tuple[str, ...] | None = None

    found = status(template)

    if found is not None and not S_ISDIR(found.mode):
        templates_dir, name = _name(template)
        names = None if name is None else (name,)

    elif found is not None:
        platen = _platen(Path(template).resolve())
        templates_dir = str(platen.templates_dir)

        try:
            names = tuple(platen.templates())
        except OSError as error:
            # Watch what stopped the walk, like a directory that can't be read or a
            # broken `.platenignore` symlink, so that the fix is seen. The press
            # reports the error itself.
            paths.append(str(error.filename or template))
        else:
            paths.extend(str(platen.templates_dir / name) for name in names)

    return Look(
        templates_dir,
        names,
        {path: status(path) for path in paths},
    )
