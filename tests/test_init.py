from logging import NullHandler, getLogger


def test_package_logger_has_null_handler() -> None:
    """
    The package must set a logging `NullHandler` by default.

    This ensures that consuming applications don't get any logging
    output if they never configure it.
    """
    handlers = getLogger("platen").handlers
    assert any(isinstance(h, NullHandler) for h in handlers)
