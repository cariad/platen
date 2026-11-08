from contextlib import suppress
from os.path import isdir
from pathlib import Path
from typing import NoReturn

from .describe import describe
from .errors import CommandError
from .press import Target
from .watch import Watcher

PORT = 1440
"""The port that `--serve` serves on."""


def serve(target: Target) -> NoReturn:
    """
    Serve the output on localhost with crumpet, and press like a `Watcher`, reloading
    every HTML page that's open after every press, until interrupted.

    A directory of templates is pressed into a directory, which is served. Any other
    template is pressed to a file, so the directory that the file is in is served.

    crumpet only serves a directory that exists, so a missing one is created before
    serving starts, and stays even when serving can't start.

    Args:
        target: What to press, and where.

    Raises:
        KeyboardInterrupt: When interrupted, which is how serving ends.
        CommandError: When the output can't be served, or changes can't be watched.
    """
    # Importing crumpet imports Python's `http.server` and watchfiles, which would slow
    # down every press that doesn't serve.
    from crumpet import Server

    # A template that can't be found is pressed like a file, and fails like one.
    if isdir(target.template):
        directory, page = target.out, ""
    else:
        # Name the file like a press does, which ignores a trailing separator.
        path = Path(target.out)
        directory, page = str(path.parent), path.name

    try:
        # A relative path leads from the working directory, so that must still exist.
        Path(directory).absolute()

    except FileNotFoundError as error:
        raise CommandError("The working directory no longer exists") from error

    try:
        # A path that exists, but isn't a directory, is left for crumpet to refuse.
        with suppress(FileExistsError):
            Path(directory).mkdir(parents=True)

        server = Server(
            directory,
            port=PORT,
        )

    except OSError as error:
        # An error with the directory names it, and an error with the port doesn't.
        if error.filename is not None:
            raise CommandError(describe(error)) from error

        raise CommandError(f"Can't serve on port {PORT}: {describe(error)}") from error

    with server:
        Watcher(
            target,
            serving=(
                server,
                server.url_for(page),
            ),
        ).run()
