from collections.abc import Iterable
from contextlib import closing
from time import sleep
from typing import TYPE_CHECKING, NoReturn

from ..watch import Look, baseline, directories, look, unwritten
from .console import say, warn
from .errors import CommandError
from .press import Target, press, report

if TYPE_CHECKING:
    from crumpet import Server


class Watcher:
    """
    Presses, then presses again whenever anything that the last press read changes,
    until interrupted.

    Events from watchfiles only wake Platen up. Whether to press again is decided by
    looking at everything that the last press read, and comparing it with how it was
    then. Platen's outputs aren't among what it read, so writing them never causes
    another press. And the paths that events name never have to be matched to what was
    read, which symlinks and case-insensitive file systems would make unreliable.
    They're only matched to the directories being watched, to watch one again that's
    replaced.

    Every press prints a line, and a press that fails doesn't stop the watch.

    Args:
        target: What to press, and where.
        serving: The server that serves the output, and the URL that it serves the
            output at, or `None` when nothing's served. Every page that the server
            serves is reloaded after every press.
    """

    def __init__(
        self,
        target: Target,
        *,
        serving: tuple[Server, str] | None = None,
    ) -> None:
        self._serving = serving
        self._target = target

        self._failed = False
        """Whether the last watcher failed."""

        self._pressed = self._look([])
        """
        The look that the last press was pressed from. Until the first press, it's the
        look to press from.
        """

        self._read: list[str] = []
        """
        Paths to the templates that the last press read, except any that it wrote to.
        """

        self._warned = False
        """Whether Platen has warned that watchers keep failing, since one last woke."""

    def _directories(self) -> frozenset[str]:
        """
        Find the directories to watch for changes to what the last press read.

        Returns:
            The real path to each directory.

        Raises:
            CommandError: When the working directory is gone, so relative paths lead
                nowhere.
        """
        try:
            return directories(
                self._target.template,
                self._pressed.fingerprints,
            )

        except FileNotFoundError as error:
            raise CommandError(
                "The working directory no longer exists, so changes can't be watched"
            ) from error

    def _fail(self, *, replaced_failed: bool, warning: str | None = None) -> None:
        """
        Record that the watcher failed before it woke.

        A watcher that replaced one that failed mustn't be replaced as fast as it
        failed, like while a checkout keeps replacing a directory, or when every new
        watcher raises the same error at once. So it first waits as long as a wake
        without an event would.

        Args:
            replaced_failed: Whether the watcher replaced one that failed.
            warning: What to warn before waiting, once, until a watcher wakes.
        """
        if replaced_failed:
            if warning is not None and not self._warned:
                warn(warning)
                self._warned = True

            sleep(5)

        self._failed = True

    def _look(self, read: Iterable[str]) -> Look:
        """
        Look at everything that a press of the target depends on (see `look`).

        Args:
            read: Paths to the templates that the last press read, except any that it
                wrote to.

        Returns:
            The look.
        """
        return look(self._target.template, self._target.values, read)

    def _press_and_look(self, before: Look) -> None:
        """
        Press, and find what the press depends on.

        Args:
            before: The look taken just before the press, at every path that the last
                press read.
        """
        pressed = press(self._target)

        report(
            self._target,
            pressed,
            watching=True,
        )

        if self._serving is not None:
            server, _ = self._serving
            server.reload()

        self._read = unwritten(
            pressed.read,
            pressed.written,
        )

        self._pressed = baseline(
            before,
            {str(path): seen for path, seen in pressed.read.items()},
            self._look(self._read),
        )

    def _watch_once(self, watching: frozenset[str]) -> frozenset[str] | None:
        """
        Watch directories until the watcher must be replaced, looking for changes and
        pressing at every wake.

        Args:
            watching: The real path to each directory to watch.

        Returns:
            The directories that the next watcher must watch, when an event showed that
            they've changed, or `None` when they must be found again.

        Raises:
            CommandError: When changes can't be watched.
        """
        # Importing watchfiles imports anyio too, which would slow down every press that
        # doesn't watch.
        from watchfiles import Change, watch

        # Whether this watcher replaced one that failed, and hasn't woken yet.
        replaced_failed = self._failed
        self._failed = False

        # Every directory to watch is named, so none is watched recursively. Every event
        # is a reason to look, so none are filtered out. Changes that arrive together
        # wake the watcher once, after 50 milliseconds without another, for up to 1.6
        # seconds. The watcher wakes every 5 seconds without an event too, so that a
        # change that the operating system didn't report, like on a network drive, is
        # still seen.
        #
        # The options that watchfiles' environment variables could change are set, so
        # that it can't skip a directory that it can't watch, silently, or print its own
        # lines among Platen's. Only polling is left to them, which changes when Platen
        # wakes but never what it presses: `WATCHFILES_FORCE_POLLING` can make
        # watchfiles poll, like it does on WSL unless told not to, and
        # `WATCHFILES_POLL_DELAY_MS` says how often. Ctrl+C must raise
        # `KeyboardInterrupt`.
        with closing(
            watch(
                *sorted(watching),
                debounce=1_600,
                debug=False,
                ignore_permission_denied=False,
                raise_interrupt=True,
                recursive=False,
                rust_timeout=5_000,
                step=50,
                watch_filter=None,
                yield_on_timeout=True,
            ),
        ) as wakes:
            while True:
                # Look before waiting, so that a change made during a press, or before
                # this look, needn't wait for an event that's already passed. watchfiles
                # only starts watching at the first wake, after the look, so a change
                # made between them is seen at the first wake without an event instead,
                # within 5 seconds.
                now = self._look(self._read)

                if now != self._pressed:
                    self._press_and_look(now)

                    # Start a new watcher after every press. A press can read templates
                    # in other directories, and the change that caused it can have
                    # replaced a watched directory with another at the same path, like a
                    # checkout does. Linux stops watching a directory that's deleted,
                    # and the new one's number can be the same, so the directories to
                    # watch can look unchanged.
                    return None

                try:
                    changes = next(wakes)

                except FileNotFoundError:
                    # watchfiles starts watching at the first wake, so a directory that
                    # was removed after it was found, like by a checkout, is missing by
                    # then. Find the directories again.
                    self._fail(replaced_failed=replaced_failed)
                    return None

                except OSError as error:
                    raise CommandError(f"Can't watch for changes: {error}") from error

                except RuntimeError as error:
                    # watchfiles keeps an error that an event raised, like one for a
                    # file whose name isn't UTF-8 on Linux, and raises it at every wake
                    # after. A new watcher starts without it, so start one. More events
                    # like it can fail that one too, before it wakes, so that mustn't
                    # end the watch.
                    #
                    # Changes are still looked for every 5 seconds while watchers fail,
                    # so say so rather than let changes seem slow.
                    self._fail(
                        replaced_failed=replaced_failed,
                        warning=(
                            "Can't watch for changes, so looking for them every 5 "
                            f"seconds instead: {error}"
                        ),
                    )

                    return None

                replaced_failed = False
                self._warned = False

                # Start a new watcher when an event says that a watched directory was
                # deleted or added, because Linux stops watching a directory that's
                # deleted, even when another is made at the same path, like by a
                # checkout.
                #
                # An event that one was modified, like when polling sees what's in it
                # change, needs nothing.
                if any(
                    path in watching
                    for change, path in changes
                    if change is not Change.modified
                ):
                    return None

                # Start one too when a directory appears within the templates directory
                # without a press. Only an event can say so: a wake without one is for a
                # change that wasn't reported, which the look sees.
                if changes and (found := self._directories()) != watching:
                    return found

    def run(self) -> NoReturn:
        """
        Press, say that the watch has started, then watch until interrupted.

        Raises:
            KeyboardInterrupt: When interrupted, which is how watching ends.
            CommandError: When changes can't be watched.
        """
        self._press_and_look(self._pressed)

        if self._serving is not None:
            _, url = self._serving
            say(f"Serving {self._target.out!r} at {url}")

        say("Watching for changes. Press Ctrl+C to stop.")

        # The directories to watch, or `None` when they must be found again.
        watching: frozenset[str] | None = None

        while True:
            if watching is None:
                watching = self._directories()

            watching = self._watch_once(watching)
