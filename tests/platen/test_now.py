from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch, fixture, mark, raises
from rootle.testing import snapshot

from platen import (
    Platen,
    SourceDateEpochError,
    TimeZoneNotFoundError,
)


@fixture(autouse=True)
def no_source_date_epoch(monkeypatch: MonkeyPatch) -> None:
    """Unset `SOURCE_DATE_EPOCH`, which the environment running the tests might set."""
    monkeypatch.delenv(
        "SOURCE_DATE_EPOCH",
        raising=False,
    )


def test_now_is_overridden_by_value(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """A `now` in the values must override the `now()` global."""
    (tmp_path / "doc.md").write_text("{{ now }}\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {"now": "then"},
    )

    platen.press_file(
        "doc.md",
        output_dir / "doc.md",
    )

    result = (output_dir / "doc.md").read_text(encoding="utf-8")

    assert result == "then\n"


def test_now_is_same_in_every_template_of_press(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    Every call to `now()` during a press must return the same instant, in every
    template, including those that a template includes.
    """
    templates_dir = tmp_path / "source"
    templates_dir.mkdir()

    (templates_dir / ".platenignore").write_text("_*\n", encoding="utf-8")

    (templates_dir / "_part.txt").write_text(
        "{{ now().isoformat() }}\n",
        encoding="utf-8",
    )

    (templates_dir / "a.txt").write_text(
        '{{ now().isoformat() }}\n{% include "_part.txt" %}',
        encoding="utf-8",
    )

    (templates_dir / "b.txt").write_text(
        "{{ now().isoformat() }}\n",
        encoding="utf-8",
    )

    platen = Platen(
        templates_dir,
        {},
    )

    platen.press(output_dir)

    a = (output_dir / "a.txt").read_text(encoding="utf-8").splitlines()
    b = (output_dir / "b.txt").read_text(encoding="utf-8").splitlines()
    result = {*a, *b}

    assert len(result) == 1


def test_now_is_source_date_epoch_in_every_press(
    monkeypatch: MonkeyPatch,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """When `SOURCE_DATE_EPOCH` is set, `now()` must return it in every press."""
    monkeypatch.setenv(
        "SOURCE_DATE_EPOCH",
        "1766045280",
    )

    (tmp_path / "doc.md").write_text("{{ now().isoformat() }}\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {},
    )

    platen.press_file(
        "doc.md",
        output_dir / "first.md",
    )

    platen.press_file(
        "doc.md",
        output_dir / "second.md",
    )

    result = (
        (output_dir / "first.md").read_text(encoding="utf-8"),
        (output_dir / "second.md").read_text(encoding="utf-8"),
    )

    expect = (
        "2025-12-18T08:08:00+00:00\n",
        "2025-12-18T08:08:00+00:00\n",
    )

    assert result == expect


def test_now_is_time_in_time_zone(
    monkeypatch: MonkeyPatch,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """`now(time_zone)` must return the time of the press in the IANA time zone."""
    # 2026-07-01 12:00:00 UTC, when London is on British Summer Time.
    monkeypatch.setenv(
        "SOURCE_DATE_EPOCH",
        "1782907200",
    )

    (tmp_path / "doc.md").write_text(
        '{{ now("Europe/London").isoformat() }}\n',
        encoding="utf-8",
    )

    platen = Platen(
        tmp_path,
        {},
    )

    platen.press_file(
        "doc.md",
        output_dir / "doc.md",
    )

    result = (output_dir / "doc.md").read_text(encoding="utf-8")

    assert result == "2026-07-01T13:00:00+01:00\n"


def test_now_is_time_of_each_press(
    monkeypatch: MonkeyPatch,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """Each press must get a new time."""
    times: Iterator[datetime] = iter(
        [
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        ]
    )

    monkeypatch.setattr("platen.platen.pressed_at", lambda: next(times))
    (tmp_path / "doc.md").write_text("{{ now().date() }}\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {},
    )

    platen.press_file(
        "doc.md",
        output_dir / "first.md",
    )

    platen.press_file(
        "doc.md",
        output_dir / "second.md",
    )

    result = (
        (output_dir / "first.md").read_text(encoding="utf-8"),
        (output_dir / "second.md").read_text(encoding="utf-8"),
    )

    expect = (
        "2026-01-01\n",
        "2026-01-02\n",
    )

    assert result == expect


def test_now_is_time_of_press(
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    `now()` must return the time of the press as an aware `datetime` in UTC, when
    `SOURCE_DATE_EPOCH` isn't set.
    """
    (tmp_path / "doc.md").write_text("{{ now().isoformat() }}\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {},
    )

    before = datetime.now(UTC)

    platen.press_file(
        "doc.md",
        output_dir / "doc.md",
    )

    after = datetime.now(UTC)

    pressed = (output_dir / "doc.md").read_text(encoding="utf-8").strip()
    result = datetime.fromisoformat(pressed)

    assert result.tzinfo == UTC
    assert before <= result <= after


@mark.parametrize(
    "value",
    [
        "",
        " 1",
        "+1",
        "-1",
        "1.5",
        "1_000",
        "abc",
        # An Arabic-Indic digit, which `int` would accept.
        chr(0x0661),
        # Too far in the future for `datetime`.
        "99999999999999999999",
        # Too long for `int` to parse.
        "9" * 5000,
    ],
)
def test_source_date_epoch_that_is_not_valid_is_refused(
    value: str,
    monkeypatch: MonkeyPatch,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """
    A `SOURCE_DATE_EPOCH` that isn't a whole number of seconds since the Unix epoch
    must stop the press before anything is written, naming the value.
    """
    monkeypatch.setenv("SOURCE_DATE_EPOCH", value)
    (tmp_path / "doc.md").write_text("Hello\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {},
    )

    before = snapshot(tmp_path)

    with raises(SourceDateEpochError) as error:
        platen.press_file("doc.md", output_dir / "doc.md")

    assert error.value.value == value

    result = snapshot(tmp_path)

    assert result == before


@mark.parametrize(
    "time_zone",
    [
        "../zone",
        "Mars/Olympus",
    ],
)
def test_time_zone_that_is_not_known_is_refused(
    time_zone: str,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    """`now(time_zone)` must raise an error naming a time zone that isn't known."""
    (tmp_path / "doc.md").write_text("{{ now(time_zone) }}\n", encoding="utf-8")

    platen = Platen(
        tmp_path,
        {"time_zone": time_zone},
    )

    with raises(TimeZoneNotFoundError) as error:
        platen.press_file(
            "doc.md",
            output_dir / "doc.md",
        )

    result = str(error.value)

    expect = f"Time zone {time_zone!r} not found"

    assert result == expect
