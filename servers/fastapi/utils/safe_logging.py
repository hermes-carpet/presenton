import logging
import sys
from typing import Any

_CONTROL_ESCAPES = {
    code: f"\\x{code:02x}"
    for code in (*range(32), *range(127, 160))
}
_CONTROL_ESCAPES.update({9: "\\t", 0x2028: "\\u2028", 0x2029: "\\u2029"})


def _escape_controls(rendered: str) -> str:
    return rendered.replace("\r", "\\r").replace("\n", "\\n").replace("\x1b", "\\x1b").translate(_CONTROL_ESCAPES)


def _log_exception(logger, level, rendered, *, exc_info, stack_info=False, stacklevel=1, extra=None):
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    elif not isinstance(exc_info, tuple):
        exc_info = sys.exc_info()
    filename, lineno, function, stack = logger.findCaller(stack_info, stacklevel + 1)
    record = logger.makeRecord(logger.name, level, filename, lineno, rendered, (), exc_info, function, extra, stack)
    # Standard formatters reuse exc_text. Retain the original exception metadata
    # while preventing provider errors, chained exceptions and notes forging lines.
    record.exc_text = _escape_controls(logging.Formatter().formatException(exc_info))
    logger.handle(record)


def safe_log(logger: logging.Logger, level: int, message: object, *args: Any, **kwargs: Any) -> None:
    """Escape message controls after standard formatting; retain traceback metadata."""
    if not logger.isEnabledFor(level):
        return
    try:
        record = logging.LogRecord(logger.name, level, "", 0, message, args, None)
        rendered = record.getMessage()
    except Exception:
        # Logging failures must not turn an otherwise valid request into an error.
        rendered = "Unable to format diagnostic message"
    rendered = _escape_controls(rendered)
    kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 1
    if kwargs.get("exc_info"):
        _log_exception(logger, level, rendered, **kwargs)
    else:
        logger.log(level, rendered, **kwargs)


def safe_log_exception(logger: logging.Logger, message: object, *args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("exc_info", True)
    kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 1
    safe_log(logger, logging.ERROR, message, *args, **kwargs)
