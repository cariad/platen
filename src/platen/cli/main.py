from collections.abc import Sequence

from .arguments import parse
from .console import error
from .describe import describe
from .press import press, report
from .serve import serve
from .watch import Watcher


def main(argv: Sequence[str] | None = None) -> int:
    """
    Press values from a YAML file into a template, or into every template within a
    directory, on the command line.

    Nothing is printed on success, except a warning when a directory has nothing to
    press. Any failure is printed as a single line, without a traceback.

    With `--watch`, it presses, then presses again whenever anything that the last
    press read changes, until it's interrupted. Every press prints a line, and a press
    that fails doesn't stop it.

    With `--serve`, it watches like with `--watch`, and serves the output on localhost
    too, reloading every HTML page that's open after every press.

    Args:
        argv: Command-line arguments, without the program's name. Defaults to the
            arguments that the program was run with.

    Returns:
        0 on success, 1 on failure, or 130 when interrupted, which is how watching
        ends.

    Raises:
        SystemExit: When the arguments are wrong (with 2), or help/version is asked for
            (with 0).
    """
    arguments = parse(argv)

    try:
        if arguments.serve:
            serve(arguments.target)

        if arguments.watch:
            Watcher(arguments.target).run()

        return report(
            arguments.target,
            press(arguments.target),
            watching=False,
        )

    except KeyboardInterrupt:
        # Stop quietly, with the exit code that shells give an interrupted command.
        return 130

    except Exception as ex:
        # Every press reports its own failure, so only watching for changes fails here.
        error(describe(ex))
        return 1
