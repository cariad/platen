from .platen_error import PlatenError


class SourceDateEpochError(PlatenError, ValueError):
    """
    Raised before pressing when the `SOURCE_DATE_EPOCH` environment variable is set, but
    isn't a whole number of seconds since the Unix epoch.

    `SOURCE_DATE_EPOCH` replaces the time that `now()` returns in templates, so that
    builds are reproducible: https://reproducible-builds.org/specs/source-date-epoch/
    """

    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.value = value

    def __str__(self) -> str:
        return (
            f"SOURCE_DATE_EPOCH ({self.value!r}) must be a whole number of seconds "
            "since the Unix epoch"
        )
