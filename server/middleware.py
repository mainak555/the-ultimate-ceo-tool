"""
Request-ID middleware: read or generate `X-Request-ID`, bind to a contextvar
so logs can be correlated, and echo the value back on responses.
"""

import logging
import uuid

from .logging_utils import bind_request_id, clear_request_id

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    """Bind a per-request id into a contextvar visible to every log record."""

    header_name = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def _resolve_id(self, request) -> str:
        incoming = request.META.get("HTTP_X_REQUEST_ID", "").strip()
        if incoming:
            # Cap at 64 chars to avoid abuse via huge headers in logs.
            return incoming[:64]
        return uuid.uuid4().hex[:12]

    def __call__(self, request):
        request_id = self._resolve_id(request)
        token = bind_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            clear_request_id(token)
        try:
            response[self.header_name] = request_id
        except Exception:
            # Some response types (e.g. streaming) may not allow header mutation
            # after the body has started — never fail the request because of logging.
            logger.debug("request_id.header_set_failed", extra={"request_id": request_id})
        return response
