from pytest import mark

from platen.watch import Look, Status, baseline


def _status(inode: int) -> Status:
    """Make the status of a regular file, told apart from others by its inode."""
    return Status(
        changed=1,
        device=1,
        inode=inode,
        mode=0o100644,
        modified=1,
        size=1,
    )


@mark.parametrize(
    ("before", "after"),
    [
        (("a.md",), ("a.md", "b.md")),
        # The template appeared while the press ran.
        (None, ("a.md",)),
        # The template disappeared while the press ran.
        (("a.md",), None),
    ],
)
def test_baseline_gives_names_from_before(
    before: tuple[str, ...] | None,
    after: tuple[str, ...] | None,
) -> None:
    """
    `baseline` must give the names that the walk found before the press, because the
    press pressed those, so that a walk that changed while it ran presses again.
    """
    pressed = baseline(
        Look(
            "/templates",
            before,
            {
                "templates": _status(1),
            },
        ),
        {},
        Look(
            "/templates",
            after,
            {
                "templates": _status(1),
            },
        ),
    )

    assert pressed.names == before


def test_baseline_gives_status_from_after_to_path_not_looked_at_before() -> None:
    """
    `baseline` must give a path that the press didn't read and that wasn't looked at
    before the press, like a template that a walk after the press found for the first
    time, its status from after the press, because it couldn't be looked at before.
    """
    pressed = baseline(
        Look(
            None,
            None,
            {
                "a.md": _status(1),
            },
        ),
        {},
        Look(
            None,
            None,
            {
                "a.md": _status(1),
                "b.md": _status(2),
                "c.md": None,
            },
        ),
    )
    expect = Look(
        None,
        None,
        {
            "a.md": _status(1),
            "b.md": _status(2),
            "c.md": None,
        },
    )

    assert pressed == expect


@mark.parametrize(
    ("before", "after"),
    [
        (_status(1), _status(2)),
        # The file was missing before the press, and created while it ran.
        (None, _status(1)),
        # The file was deleted while the press ran.
        (_status(1), None),
    ],
)
def test_baseline_gives_status_from_before_to_path_known_before(
    before: Status | None,
    after: Status | None,
) -> None:
    """
    `baseline` must give a path that was looked at before the press its status from
    then, even when it was missing, so that a change made while the press ran is out
    of date as soon as the press ends.
    """
    pressed = baseline(
        Look(
            None,
            None,
            {
                "a.md": before,
                "b.md": _status(3),
            },
        ),
        {},
        Look(
            None,
            None,
            {
                "a.md": after,
                "b.md": _status(3),
            },
        ),
    )

    expect = Look(
        None,
        None,
        {"a.md": before, "b.md": _status(3)},
    )

    assert pressed == expect


@mark.parametrize(
    ("before", "read"),
    [
        # The press read the template for the first time, so it wasn't looked at
        # before the press.
        ({}, _status(2)),
        # The template changed after the look before the press, but before the press
        # read it.
        ({"a.md": _status(1)}, _status(2)),
        # The template was missing when the press tried to read it, and was created
        # before the press ended.
        ({}, None),
    ],
)
def test_baseline_gives_status_from_read_to_path_that_press_read(
    before: dict[str, Status | None],
    read: Status | None,
) -> None:
    """
    `baseline` must give a template that the press read its status from just before the
    press read it, rather than from before or after the press, so that a change made
    after the press read it is out of date as soon as the press ends.
    """
    pressed = baseline(
        Look(
            None,
            None,
            before,
        ),
        {"a.md": read},
        Look(
            None,
            None,
            {
                "a.md": _status(3),
            },
        ),
    )

    expect = Look(
        None,
        None,
        {"a.md": read},
    )

    assert pressed == expect


@mark.parametrize(
    ("before", "after"),
    [
        # A symlink to the template's directory was retargeted while the press ran.
        ("/a", "/b"),
        # The template appeared while the press ran.
        (None, "/a"),
        # The template disappeared while the press ran.
        ("/a", None),
    ],
)
def test_baseline_gives_templates_dir_from_before(
    before: str | None,
    after: str | None,
) -> None:
    """
    `baseline` must give the templates directory from before the press, because the
    press loaded templates from there, so that one that changed while it ran presses
    again.
    """
    pressed = baseline(
        Look(
            before,
            ("doc.md",),
            {
                "doc.md": _status(1),
            },
        ),
        {},
        Look(
            after,
            ("doc.md",),
            {
                "doc.md": _status(1),
            },
        ),
    )

    assert pressed.templates_dir == before


def test_baseline_leaves_out_path_not_looked_at_after() -> None:
    """
    `baseline` must leave out a path that was looked at before the press but not after,
    like a template that the press no longer includes, so that it's no longer watched.
    """
    pressed = baseline(
        Look(None, None, {"a.md": _status(1), "b.md": _status(2)}),
        {},
        Look(None, None, {"a.md": _status(1)}),
    )
    expect = Look(
        None,
        None,
        {"a.md": _status(1)},
    )

    assert pressed == expect
