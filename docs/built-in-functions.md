# Built-in functions

Platen adds these functions to every template, alongside [Jinja's own](https://jinja.palletsprojects.com/en/stable/templates/#list-of-global-functions).

## `now`

`now()` returns the time of the press, as an aware [`datetime`](https://docs.python.org/3/library/datetime.html#datetime-objects) in UTC.

Pass the name of an [IANA time zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) to get it in that zone instead, and format it with `datetime`'s own methods, like `strftime`.

For example:

```markdown
Last updated {{ now("Europe/London").strftime("%d %B %Y at %H:%M") }}.
```

Presses to:

```markdown
Last updated 18 November 2026 at 08:37.
```

Every call to `now()` during a press returns the same instant, in every template, so two templates can't disagree about the minute.

When the `SOURCE_DATE_EPOCH` environment variable is set, `now()` returns that time instead of reading the clock, as [Reproducible Builds](https://reproducible-builds.org/specs/source-date-epoch/) describes.

!!! note "`SOURCE_DATE_EPOCH`"

    Some environments set `SOURCE_DATE_EPOCH` without being asked, like Nix shells. If your documents are dated 1970 or 1980, check if it's set.
