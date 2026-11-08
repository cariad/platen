from collections.abc import Mapping
from os import PathLike, fspath
from typing import Any, cast
from warnings import catch_warnings

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .describe import escape, yaml_problem
from .errors import CommandError


def load_values(path: PathLike[str] | str) -> Mapping[str, Any]:
    """
    Load values from a YAML file.

    Args:
        path: Path to the values file, as it was given.

    Returns:
        The values. An empty file holds no values.

    Raises:
        CommandError: When the file isn't valid YAML, doesn't hold a mapping, or has a
            key that isn't a string.
        OSError: When the file can't be read.
    """
    name = fspath(path)

    with open(name, "rb") as stream:
        try:
            # ruamel.yaml warns over many lines about some documents that it loads
            # anyway, like ones that reuse an anchor.
            with catch_warnings(action="ignore"):
                values: object = YAML(typ="safe").load(stream)  # pyright: ignore[reportUnknownMemberType]
        except YAMLError as error:
            raise CommandError(yaml_problem(error, name)) from error
        except (AssertionError, RecursionError) as error:
            # ruamel.yaml asserts that it reads a document's YAML version, and recurses
            # into nested collections.
            raise CommandError(f"{error} ({escape(name)})") from error

    if values is None:
        return {}

    if not isinstance(values, Mapping):
        raise CommandError(
            f"Values file {name!r} must hold a mapping of names to values"
        )

    for key in cast(Mapping[object, Any], values):
        if not isinstance(key, str):
            raise CommandError(
                f"Values file {name!r} has a key that isn't a string: {key!r}"
            )

    return cast(Mapping[str, Any], values)
