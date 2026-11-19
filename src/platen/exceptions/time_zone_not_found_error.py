from .platen_error import PlatenError


class TimeZoneNotFoundError(PlatenError, ValueError):
    """
    Raised when a template asks `now()` for a time zone that isn't a known IANA time
    zone, like "Europe/London".
    """

    def __init__(self, time_zone: str) -> None:
        super().__init__(time_zone)
        self.time_zone = time_zone

    def __str__(self) -> str:
        return f"Time zone {self.time_zone!r} not found"
