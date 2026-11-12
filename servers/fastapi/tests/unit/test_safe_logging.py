import inspect
import logging

import pytest

from utils.safe_logging import safe_log, safe_log_exception

LOGGER = logging.getLogger("presenton.safe-logging.test")


@pytest.mark.parametrize(("message", "args", "expected"), [
    ("value=%s count=%d 100%%", ("normal", 4), "value=normal count=4 100%"),
    ("name=%(name)s", ({"name": "Control"},), "name=Control"),
    ("literal 100%%", (), "literal 100%%"),
    ("value=%r", ("control\nnext",), "value='control\\nnext'"),
])
def test_safe_logging_preserves_standard_formatting(caplog, message, args, expected):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        safe_log(LOGGER, logging.INFO, message, *args)
    assert caplog.records[-1].getMessage() == expected


def test_safe_logging_escapes_formatted_str_and_repr(caplog):
    class Value:
        def __str__(self):
            return "str\nFORGED\r\x1b[2J"

        def __repr__(self):
            return "repr\nFORGED\r\x1b[2J"

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        safe_log(LOGGER, logging.INFO, "%s %r", Value(), Value())
    assert caplog.records[-1].getMessage() == "str\\nFORGED\\r\\x1b[2J repr\\nFORGED\\r\\x1b[2J"


def test_disabled_log_does_not_format_values(caplog):
    class Explosive:
        def __str__(self):
            raise AssertionError("disabled message formatted")

        __repr__ = __str__

    with caplog.at_level(logging.ERROR, logger=LOGGER.name):
        safe_log(LOGGER, logging.INFO, Explosive(), Explosive())
    assert not caplog.records


def test_log_controls_are_visible_without_escaping_localized_text(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        safe_log(LOGGER, logging.INFO, "%s", "日本語 café\u2028\u2029\x85\t\b")
    assert caplog.records[-1].getMessage() == "日本語 café\\u2028\\u2029\\x85\\t\\x08"


def test_safe_logging_preserves_callsite_and_explicit_stacklevel(caplog):
    def wrapper():
        safe_log(LOGGER, logging.INFO, "wrapped", stacklevel=2, extra={"event_type": "control"})

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        line = inspect.currentframe().f_lineno + 1
        wrapper()
    record = caplog.records[-1]
    assert record.pathname == __file__
    assert record.lineno == line
    assert record.event_type == "control"


def test_exception_logging_preserves_traceback_and_escapes_message(caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER.name):
        try:
            raise ValueError("conversion control")
        except ValueError:
            safe_log_exception(LOGGER, "failed for %s", "id\nFORGED")
    record = caplog.records[-1]
    assert record.getMessage() == "failed for id\\nFORGED"
    assert isinstance(record.exc_info[1], ValueError)
    assert record.pathname == __file__


def test_malformed_diagnostic_does_not_break_the_request(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        safe_log(LOGGER, logging.INFO, "count=%d", "not a number")
    assert caplog.records[-1].getMessage() == "Unable to format diagnostic message"


def test_exception_traceback_cannot_introduce_forged_lines_or_terminal_controls(caplog):
    error = ValueError("provider error" + chr(10) + "FORGED record" + chr(27) + "[2J")
    error.add_note("request note" + chr(13) + chr(10) + "FORGED note")
    with caplog.at_level(logging.ERROR, logger=LOGGER.name):
        try:
            raise error
        except ValueError:
            safe_log_exception(LOGGER, "provider failed", extra={"event_type": "provider"})
    record = caplog.records[-1]
    rendered = logging.Formatter("%(levelname)s %(message)s").format(record)
    assert "\nFORGED" not in rendered
    assert "\r" not in rendered and "\x1b" not in rendered
    assert "\\nFORGED record\\x1b[2J" in rendered
    assert "Traceback" in rendered and "ValueError" in rendered
    assert record.exc_info[1] is error and record.event_type == "provider"
    assert record.pathname == __file__
