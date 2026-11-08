from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch, mark
from rootle.testing import snapshot

from platen.cli import main


@mark.parametrize(
    "template",
    [
        "templates",
        "templates/",
        "./templates",
        "{cwd}/templates",
        # A symlink to `templates`, which the test creates beside it.
        "link",
        "templates/sub/..",
    ],
)
def test_presses_directory(
    template: str,
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen --templates templates --values values.yaml --out build` must press every
    file within the directory to the same path within the output, however the
    directory is named, print nothing, and exit with 0.

    Templates that are included must be found relative to the directory, wherever the
    template that includes them is, and be pressed too.
    """
    templates = project / "templates"
    (templates / "sub").mkdir(parents=True)
    (templates / "_badge.txt").write_text("Badge: {{ status }}\n", encoding="utf-8")

    (templates / "index.md").write_text(
        '# {{ name }}\n\n{% include "_badge.txt" %}\n',
        encoding="utf-8",
    )

    (templates / "sub" / "page.md").write_text(
        '{% include "_badge.txt" %}',
        encoding="utf-8",
    )

    (project / "link").symlink_to("templates")

    exit_code = main(
        [
            "--templates",
            template.format(cwd=project),
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }
    expect = {
        Path("_badge.txt"): b"Badge: passing\n",
        Path("index.md"): b"# platen\n\nBadge: passing\n",
        Path("sub/page.md"): b"Badge: passing\n",
    }

    assert pressed == expect


def test_presses_empty_values(capsys: CaptureFixture[str], project: Path) -> None:
    """An empty values file must be read as no values, rather than refused."""
    (project / "doc.template").write_text("Hello, world!\n", encoding="utf-8")
    (project / "empty.yaml").write_text("", encoding="utf-8")

    exit_code = main(
        [
            "--template",
            "doc.template",
            "--values",
            "empty.yaml",
            "--out",
            "doc.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    result = (project / "doc.md").read_text(encoding="utf-8")

    assert result == "Hello, world!\n"


def test_presses_readme_next_to_template(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    `platen --template README.template --values values.yaml --out README.md` must press
    the README next to its template, resolving its include from the template's
    directory, print nothing, and exit with 0.
    """
    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_presses_symlink_to_template_within_directory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A template that's a symlink to a file in its own directory must be pressed."""
    (project / "link.template").symlink_to("README.template")

    exit_code = main(
        [
            "--template",
            "link.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# platen\n\nBuild: passing\n"


def test_presses_template_in_subdirectory(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A template in a subdirectory must resolve its includes from that subdirectory, and
    must be pressed to an output of any name, creating missing directories.
    """
    docs = project / "docs"
    docs.mkdir()
    (docs / "page.template").write_text('{% include "_part.txt" %}', encoding="utf-8")
    (docs / "_part.txt").write_text("Hello from {{ name }}\n", encoding="utf-8")

    exit_code = main(
        [
            "--template",
            "docs/page.template",
            "--values",
            "values.yaml",
            "--out",
            "build/site/index.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    pressed = (project / "build" / "site" / "index.md").read_text(encoding="utf-8")
    assert pressed == "Hello from platen\n"


@mark.parametrize(
    ("ignore", "expect"),
    [
        # Without a `.platenignore` file, the values file is pressed like any other
        # template, so its own `{{ name }}` is rendered.
        (
            None,
            {
                Path("index.md"): b"# platen\n",
                Path("values.yaml"): b'name: platen\ngreeting: "platen"\n',
            },
        ),
        (
            "values.yaml\n",
            {
                Path("index.md"): b"# platen\n",
            },
        ),
    ],
)
def test_presses_values_file_within_directory(
    ignore: str | None,
    expect: dict[Path, bytes],
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A values file within the directory must be pressed as a template too, unless a
    `.platenignore` file lists it.
    """
    templates = project / "templates"
    templates.mkdir()
    (templates / "index.md").write_text("# {{ name }}\n", encoding="utf-8")

    (templates / "values.yaml").write_text(
        'name: platen\ngreeting: "{{ name }}"\n',
        encoding="utf-8",
    )

    if ignore is not None:
        (templates / ".platenignore").write_text(ignore, encoding="utf-8")

    exit_code = main(
        [
            "--templates",
            "templates",
            "--values",
            "templates/values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.err == ""

    build = project / "build"

    pressed = {
        path.relative_to(build): path.read_bytes()
        for path in build.rglob("*")
        if path.is_file()
    }

    assert pressed == expect


@mark.filterwarnings("error")
def test_presses_values_that_reuse_anchor(
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """A values file that reuses an anchor must press without printing a warning."""
    (project / "values.yaml").write_text(
        "name: &x a\nstatus: &x passing\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--template",
            "README.template",
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    pressed = (project / "README.md").read_text(encoding="utf-8")
    assert pressed == "# a\n\nBuild: passing\n"


@mark.parametrize(
    ("option", "template", "method"),
    [
        (
            "--templates",
            ".",
            "press",
        ),
        (
            "--template",
            "README.template",
            "press_file",
        ),
    ],
)
def test_stops_quietly_when_interrupted(
    option: str,
    template: str,
    method: str,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    project: Path,
) -> None:
    """An interrupt must stop `platen` quietly, with exit code 130."""

    def interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(f"platen.Platen.{method}", interrupt)

    exit_code = main(
        [
            option,
            template,
            "--values",
            "values.yaml",
            "--out",
            "README.md",
        ],
    )

    assert exit_code == 130

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@mark.parametrize(
    ("template", "files"),
    [
        ("docs", {}),
        ("docs/", {}),
        ("docs", {".platenignore": "*.md\n", "page.md": "Hello\n"}),
    ],
)
def test_warns_when_directory_has_nothing_to_press(
    template: str,
    files: dict[str, str],
    capsys: CaptureFixture[str],
    project: Path,
) -> None:
    """
    A directory with nothing to press must print a warning that names it as it was
    given, write nothing, and exit with 0.
    """
    (project / "docs").mkdir()

    for name, body in files.items():
        (project / "docs" / name).write_text(body, encoding="utf-8")

    before = snapshot(project)

    exit_code = main(
        [
            "--templates",
            template,
            "--values",
            "values.yaml",
            "--out",
            "build",
        ],
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    assert captured.out == ""

    assert captured.err == (
        f"platen: warning: Nothing to press in '{template}': it's empty, or "
        "everything in it is ignored\n"
    )

    result = snapshot(project)

    assert result == before
