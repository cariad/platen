from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from urllib.parse import urlsplit

from pytest import CaptureFixture
from rootle.testing import snapshot

from platen.cli import main


def assert_fails(
    capsys: CaptureFixture[str],
    directory: Path,
    arguments: list[str],
    message: str,
) -> None:
    """
    Assert that running `platen` fails with exit code 1 and a single line of error on
    stderr, prints nothing on stdout, and changes nothing within a directory.

    Only use this for a press that stops on its first template: a press keeps every
    template that it pressed before the error.
    """
    before = snapshot(directory)

    exit_code = main(arguments)

    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"platen: error: {message}\n"

    result = snapshot(directory)

    assert result == before


def create_files(
    directory: Path,
    files: dict[str, str],
    links: dict[str, str] | None = None,
) -> None:
    """
    Create files within a directory, and then symlinks, creating missing directories
    and replacing whatever is already at each symlink's path.
    """
    for name, body in files.items():
        path = directory / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(body, encoding="utf-8")

    for name, target in (links or {}).items():
        path = directory / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.unlink(missing_ok=True)
        path.symlink_to(target)


def http_get(url: str) -> HTTPResponse:
    """
    Request a URL from the server that it names, rather than through any proxy that the
    environment names, and give up on any read that waits for more than 10 seconds.
    """
    _, host, path, query, _ = urlsplit(url)
    connection = HTTPConnection(host, timeout=10)
    connection.request("GET", f"{path}?{query}" if query else path)
    return connection.getresponse()
