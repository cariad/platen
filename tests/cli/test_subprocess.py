import os
import sys
from pathlib import Path
from queue import Empty, Queue
from re import fullmatch
from signal import SIGINT
from subprocess import PIPE, Popen, run
from threading import Thread
from time import sleep

from notwork import free_port
from pytest import raises
from rootle.testing import save_atomically

from tests.cli.helpers import http_get
from tests.cli.lines import (
    PRESSED_FILE,
    WATCHING,
)


def _page(url: str) -> str:
    """Get a page as it was pressed, without crumpet's script to reload it."""
    with http_get(url) as response:
        page = response.read().decode()

    return page.partition("<script")[0]


def test_presses_without_importing_watchfiles_or_crumpet(project: Path) -> None:
    """
    A press without `--watch` or `--serve` must never import watchfiles, or the anyio
    that it imports, or crumpet, or the Python `http.server` that crumpet imports, which
    would slow down every press.
    """
    # Only a fresh interpreter shows what a press imports, because this one imports
    # them all for other tests.
    script = (
        "import sys\n"
        "from platen.cli import main\n"
        "exit_code = main()\n"
        "modules = {'anyio', 'crumpet', 'http.server', 'watchfiles'}\n"
        "imported = modules & sys.modules.keys()\n"
        "sys.exit(99 if imported else exit_code)\n"
    )

    result = run(
        [
            sys.executable,
            "-c",
            script,
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
        capture_output=True,
        check=False,
        cwd=project,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_serve_serves_pages_with_real_server(project: Path) -> None:
    """
    `platen --serve` must serve a page that it pressed through crumpet, serve it as it
    was pressed again once a change has been pressed, and stop quietly with exit code
    130 on SIGINT, like from Ctrl+C.
    """
    (project / "page.template").write_text(
        "<body>{{ name }}</body>\n",
        encoding="utf-8",
    )

    port = free_port()
    pressed = "platen: Pressed 'page.template' to 'page.html'\n"

    with Popen(
        [
            sys.executable,
            "-c",
            # A process that's started in the background inherits SIGINT ignored, and
            # Python then never raises `KeyboardInterrupt`. A free port, rather than
            # the one that Platen might be serving on for real.
            "import signal, sys; "
            "signal.signal(signal.SIGINT, signal.default_int_handler); "
            "import platen.cli; "
            f"platen.cli.serve.PORT = {port}; "
            "sys.exit(platen.cli.main())",
            "--template",
            "page.template",
            "--values",
            "values.yaml",
            "--out",
            "page.html",
            "--serve",
        ],
        cwd=project,
        # watchfiles' environment variables can change how it watches.
        env={k: v for k, v in os.environ.items() if not k.startswith("WATCHFILES_")},
        stderr=PIPE,
        stdout=PIPE,
        text=True,
    ) as process:
        assert process.stderr is not None
        assert process.stdout is not None

        stderr = process.stderr
        lines: Queue[str] = Queue()

        def read() -> None:
            for line in stderr:
                # Python's own server prints a line for every request, among Platen's.
                if not fullmatch(r"\S+ - - \[[^\]]*\] .*\n", line):
                    lines.put(line)

        reader = Thread(daemon=True, target=read)
        reader.start()

        try:
            result = lines.get(timeout=10)

            assert result == pressed

            result = lines.get(timeout=10)

            assert result == (
                f"platen: Serving 'page.html' at http://localhost:{port}/page.html\n"
            )

            result = lines.get(timeout=10)

            assert result == WATCHING

            url = f"http://localhost:{port}/page.html"
            result = _page(url)

            assert result == "<body>platen</body>\n"

            # watchfiles wakes Platen 5 seconds after it starts without a change, so the
            # change is pressed by then, even if watchfiles misses it.
            save_atomically(project / "values.yaml", "name: second\nstatus: passing\n")
            result = lines.get(timeout=10)

            assert result == pressed

            result = _page(url)

            assert result == "<body>second</body>\n"

            process.send_signal(SIGINT)
            exit_code = process.wait(timeout=10)
        finally:
            process.kill()
            process.wait()
            reader.join(timeout=10)

        out = process.stdout.read()

    assert exit_code == 130
    assert out == ""

    # Nothing more was printed, like a traceback.
    assert lines.empty()


def test_serve_serves_where_output_symlink_leads_now(project: Path) -> None:
    """
    `platen --serve` must serve the directory that a symlink on the output's path leads
    to now, once it's changed to lead elsewhere, rather than where it led when serving
    started, since that's where the next press writes.
    """
    (project / "page.template").write_text(
        "<body>{{ name }}</body>\n",
        encoding="utf-8",
    )

    (project / "before").mkdir()
    (project / "after").mkdir()
    site = project / "site"
    site.symlink_to("before")

    port = free_port()
    pressed = "platen: Pressed 'page.template' to 'site/page.html'\n"

    with Popen(
        [
            sys.executable,
            "-c",
            # A process that's started in the background inherits SIGINT ignored, and
            # Python then never raises `KeyboardInterrupt`. A free port, rather than
            # the one that Platen might be serving on for real.
            "import signal, sys; "
            "signal.signal(signal.SIGINT, signal.default_int_handler); "
            "import platen.cli; "
            f"platen.cli.serve.PORT = {port}; "
            "sys.exit(platen.cli.main())",
            "--template",
            "page.template",
            "--values",
            "values.yaml",
            "--out",
            "site/page.html",
            "--serve",
        ],
        cwd=project,
        # watchfiles' environment variables can change how it watches.
        env={k: v for k, v in os.environ.items() if not k.startswith("WATCHFILES_")},
        stderr=PIPE,
        stdout=PIPE,
        text=True,
    ) as process:
        assert process.stderr is not None
        assert process.stdout is not None

        stderr = process.stderr
        lines: Queue[str] = Queue()

        def read() -> None:
            for line in stderr:
                # Python's own server prints a line for every request, among Platen's.
                if not fullmatch(r"\S+ - - \[[^\]]*\] .*\n", line):
                    lines.put(line)

        reader = Thread(daemon=True, target=read)
        reader.start()

        try:
            result = lines.get(timeout=10)

            assert result == pressed

            result = lines.get(timeout=10)

            assert result == (
                f"platen: Serving 'site/page.html' at http://localhost:{port}/page.html\n"
            )

            result = lines.get(timeout=10)

            assert result == WATCHING

            url = f"http://localhost:{port}/page.html"
            result = _page(url)

            assert result == "<body>platen</body>\n"

            # The symlink isn't among what the press read, so changing it presses
            # nothing. The change to the values does, through the symlink, to where it
            # leads now. watchfiles wakes Platen 5 seconds after it starts without a
            # change, so the change is pressed by then, even if watchfiles misses it.
            site.unlink()
            site.symlink_to("after")
            save_atomically(project / "values.yaml", "name: second\nstatus: passing\n")
            result = lines.get(timeout=10)

            assert result == pressed

            result = _page(url)

            assert result == "<body>second</body>\n"

            process.send_signal(SIGINT)
            exit_code = process.wait(timeout=10)
        finally:
            process.kill()
            process.wait()
            reader.join(timeout=10)

        out = process.stdout.read()

    assert exit_code == 130
    assert out == ""

    # Nothing more was printed, like a traceback.
    assert lines.empty()

    result = (project / "after" / "page.html").read_bytes()

    assert result == b"<body>second</body>\n"


def test_watch_presses_again_with_real_watcher(project: Path) -> None:
    """
    `platen --watch` must press again as soon as watchfiles sees a change, rather than
    at its next timeout, and press nothing more until something else changes, then
    stop quietly with exit code 130 on SIGINT, like from Ctrl+C.
    """
    with Popen(
        [
            sys.executable,
            "-c",
            # A process that's started in the background inherits SIGINT ignored, and
            # Python then never raises `KeyboardInterrupt`.
            "import signal, sys; "
            "signal.signal(signal.SIGINT, signal.default_int_handler); "
            "from platen.cli import main; "
            "sys.exit(main())",
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
            "--watch",
        ],
        cwd=project,
        # watchfiles' environment variables can change how it watches.
        env={k: v for k, v in os.environ.items() if not k.startswith("WATCHFILES_")},
        stderr=PIPE,
        stdout=PIPE,
        text=True,
    ) as process:
        assert process.stderr is not None
        assert process.stdout is not None

        stderr = process.stderr
        lines: Queue[str] = Queue()

        def read() -> None:
            for line in stderr:
                lines.put(line)

        reader = Thread(daemon=True, target=read)
        reader.start()

        try:
            result = lines.get(timeout=10)

            assert result == PRESSED_FILE

            result = lines.get(timeout=10)

            assert result == WATCHING

            # Give watchfiles time to start, so that it sees the change. It wakes Platen
            # 5 seconds after it starts without a change, so only seeing the change can
            # press this soon. The time to start and the time to press must stay within
            # those 5 seconds, so give more to starting, which a busy machine can slow,
            # than to pressing, which starts about 50 milliseconds after one change.
            sleep(2)
            save_atomically(project / "values.yaml", "name: first\nstatus: passing\n")
            result = lines.get(timeout=2)

            assert result == PRESSED_FILE

            # A new watcher starts after every press. Give it time to start too, so
            # that only it can see the next change, rather than Platen's look before
            # it starts.
            sleep(2)
            save_atomically(project / "values.yaml", "name: second\nstatus: passing\n")
            result = lines.get(timeout=2)

            assert result == PRESSED_FILE

            # Nothing else changes, so nothing more is pressed, however Platen wakes.
            with raises(Empty):
                lines.get(timeout=1)

            process.send_signal(SIGINT)
            exit_code = process.wait(timeout=10)
        finally:
            process.kill()
            process.wait()
            reader.join(timeout=10)

        out = process.stdout.read()

    assert exit_code == 130
    assert out == ""

    # Nothing more was printed, like a traceback.
    assert lines.empty()

    pressed = (project / "README.md").read_bytes()
    assert pressed == b"# second\n\nBuild: passing\n"
