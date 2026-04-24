"""
Shared logging utilities: request-id contextvar + JSON formatter + logging filter.

Usage:
- `bind_request_id(value)` and `clear_request_id()` are called by `RequestIdMiddleware`.
- `RequestIdFilter` injects the current request id onto every LogRecord.
- `JsonFormatter` is the single formatter used by the console handler in
  `config/settings.py` LOGGING.
"""

import logging
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger


_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def bind_request_id(value: str) -> object:
    """Bind the current request id; returns a token that can be used to reset."""
    return _request_id.set(value or "-")


def clear_request_id(token: object = None) -> None:
    """Reset the request id to the default sentinel."""
    if token is not None:
        try:
            _request_id.reset(token)  # type: ignore[arg-type]
            return
        except (ValueError, LookupError):
            pass
    _request_id.set("-")


def get_request_id() -> str:
    """Return the current request id (or '-' when no request is active)."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Attach the active request id to every log record as `record.request_id`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that always includes timestamp, level, logger name, and request_id."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["request_id"] = getattr(record, "request_id", "-")
