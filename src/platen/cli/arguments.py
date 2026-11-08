from argparse import SUPPRESS, Action, ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .console import PROG
from .press import Target
from .serve import PORT


class _VersionAction(Action):
    """
    Print Platen's version and exit, like argparse's own "version" action, but only
    look the version up when it's asked for.
    """

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        try:
            installed = version("platen")
        except PackageNotFoundError:
            # Platen can run without its package's metadata, like from a source tree.
            installed = "unknown"

        print(f"{parser.prog} {installed}")
        parser.exit()


@dataclass(frozen=True, slots=True)
class Arguments:
    """The command line, parsed."""

    serve: bool
    """Whether to serve the output, and press again whenever anything changes."""

    target: Target
    """What to press, and where."""

    watch: bool
    """Whether to press again whenever anything changes."""


def parse(argv: Sequence[str] | None) -> Arguments:
    """
    Parse the command line.

    Args:
        argv: Command-line arguments, without the program's name, or `None` for the
            arguments that the program was run with.

    Returns:
        The arguments.

    Raises:
        SystemExit: When the arguments are wrong (with 2), or help/version is asked for
            (with 0).
    """
    parser = ArgumentParser(
        # Prefixes like "--temp" are ambiguous between the template's two names, and
        # any prefix would break whenever a new option shared it.
        allow_abbrev=False,
        description="Press structured data into documents.",
        prog=PROG,
    )

    parser.add_argument(
        "--version",
        action=_VersionAction,
        default=SUPPRESS,
        help="show program's version number and exit",
        nargs=0,
    )

    parser.add_argument(
        "--template",
        "--templates",
        help="path to the template or directory of templates to press",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--values",
        help="path to the YAML file of values to press into the templates",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--out",
        help="path to the file or directory to press into",
        metavar="PATH",
        required=True,
    )

    parser.add_argument(
        "--watch",
        action="store_true",
        help="press again whenever the templates or values change, until interrupted",
    )

    parser.add_argument(
        "--serve",
        action="store_true",
        help=(
            f"serve the output on http://localhost:{PORT}/ and press again like "
            "--watch, reloading every open HTML page after every press"
        ),
    )

    arguments = parser.parse_args(argv)

    return Arguments(
        serve=arguments.serve,
        target=Target(
            template=arguments.template,
            values=arguments.values,
            out=arguments.out,
        ),
        watch=arguments.watch,
    )
