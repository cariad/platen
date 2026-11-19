from datetime import UTC, datetime
from os import environ
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .exceptions import SourceDateEpochError, TimeZoneNotFoundError


def as_time_zone(
    instant: datetime,
    time_zone: str | None,
) -> datetime:
    """
    Express an instant as the local time in a time zone.

    Args:
        instant: The instant, which must be aware.
        time_zone: Name of an IANA time zone, like "Europe/London", or `None` for UTC.

    Returns:
        The same instant, in the time zone.

    Raises:
        TimeZoneNotFoundError: When `time_zone` isn't a known time zone.
    """
    if time_zone is None:
        return instant.astimezone(UTC)

    try:
        zone_info = ZoneInfo(time_zone)

    # `ZoneInfo` raises `ValueError` for a name that can't be a key, like "../zone".
    except ValueError, ZoneInfoNotFoundError:
        raise TimeZoneNotFoundError(time_zone) from None

    return instant.astimezone(zone_info)


def pressed_at() -> datetime:
    """
    Get the time of the current press.

    Uses the time in the `SOURCE_DATE_EPOCH` environment variable when it's set, so
    that builds are reproducible (https://reproducible-builds.org/specs/source-date-epoch/),
    or the current time when it isn't.

    Returns:
        The time, in UTC.

    Raises:
        SourceDateEpochError: When `SOURCE_DATE_EPOCH` is set, but isn't a whole number
            of seconds since the Unix epoch.
    """
    if (value := environ.get("SOURCE_DATE_EPOCH")) is None:
        return datetime.now(UTC)

    # The specification allows only ASCII digits, which `int` alone doesn't insist on:
    # it accepts signs, spaces, underscores and other scripts' digits too.
    if not (value.isascii() and value.isdigit()):
        raise SourceDateEpochError(value)

    try:
        return datetime.fromtimestamp(int(value), UTC)

    # A time beyond what `datetime` or the platform can hold.
    except OSError, OverflowError, ValueError:
        raise SourceDateEpochError(value) from None
