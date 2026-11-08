import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from errno import ENOENT
from itertools import count
from os import PathLike, fspath, strerror
from os.path import isdir
from typing import Self, cast

from pytest import MonkeyPatch, fail

from tests.cli.lines import RELOADED


@dataclass
class FakeServer:
    """
    A fake crumpet `Server`, which serves nothing, but records how it was created and
    used. Every reload writes `RELOADED` to stderr, so that a test can see where it
    falls among Platen's own lines.
    """

    directory: PathLike[str] | str
    """The directory that the server was created to serve."""

    port: int
    """The port that the server was created to serve on."""

    entered: bool = False
    """Whether the server started serving."""

    exited: bool = False
    """Whether the server stopped serving."""

    reloads: int = 0
    """How many times the server reloaded every page."""

    def __enter__(self) -> Self:
        """Record that the server started serving."""
        self.entered = True
        return self

    def __exit__(self, *_: object) -> None:
        """Record that the server stopped serving."""
        self.exited = True

    def reload(self) -> None:
        """Record a reload, and write `RELOADED` to stderr."""
        self.reloads += 1
        print(RELOADED, end="", file=sys.stderr)

    @property
    def url(self) -> str:
        """The URL that the real server gives for the port."""
        return f"http://localhost:{self.port}/"

    def url_for(self, path: PathLike[str] | str) -> str:
        """
        The URL that the real server gives for a path within the directory, for a path
        that it doesn't need to quote, since quoting is the real server's job.
        """
        return self.url + fspath(path)


def serving(
    monkeypatch: MonkeyPatch,
    error: OSError | None = None,
) -> list[FakeServer]:
    """
    Replace crumpet's `Server` with `FakeServer`, which serves nothing, and return every
    server that's created. With `error`, creating a server raises it instead, like when
    the port is in use.

    Like crumpet, creating a server for a directory that doesn't exist fails. Platen
    must name the port, and leave watching off, so creating a server any other way fails
    too.
    """
    servers: list[FakeServer] = []

    def create(directory: PathLike[str] | str, *, port: int) -> FakeServer:
        if not isdir(directory):
            raise FileNotFoundError(ENOENT, strerror(ENOENT), fspath(directory))

        if error is not None:
            raise error

        server = FakeServer(directory, port)
        servers.append(server)
        return server

    # Platen imports `Server` from crumpet only when it serves.
    monkeypatch.setattr("crumpet.Server", create)
    return servers


def watching(
    monkeypatch: MonkeyPatch,
    *steps: Callable[[], object],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    """
    Replace watchfiles' `watch` with a fake that takes the next step at each wake,
    rather than waiting for a change, and return the paths and options that each watch
    started with. No time passes. A step that returns a set wakes with those events,
    and any other wakes with none.

    Like watchfiles, a watch starts at its first wake, so a step that raises there, like
    `FileNotFoundError` for a missing path, fails the start. A watch that restarts
    carries on with the next step. Once every step is taken, the next wake raises
    `KeyboardInterrupt`, like Ctrl+C.

    Platen starts a new watch after every press, so a watch that keeps being created
    without waking means that every press causes another, forever. That fails the test,
    rather than hanging it.
    """
    created = count()
    started: list[tuple[tuple[str, ...], dict[str, object]]] = []
    remaining = iter(steps)

    def wakes(
        paths: tuple[str, ...],
        options: dict[str, object],
    ) -> Iterator[set[object]]:
        started.append((paths, options))

        for step in remaining:
            events = step()
            yield cast(set[object], events) if isinstance(events, set) else set()

        raise KeyboardInterrupt

    def watch(*paths: str, **options: object) -> Iterator[set[object]]:
        # Each step can cause a press, a restart, or both, and the first watch starts
        # after the first press.
        if next(created) > 2 * len(steps) + 2:
            fail("Platen kept pressing without being woken")

        return wakes(paths, options)

    monkeypatch.setattr("watchfiles.watch", watch)
    return started
