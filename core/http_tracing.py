"""Shared HTTP span enrichment/error helpers for outbound integrations.

This module standardizes how provider clients (Trello, Jira, future providers)
attach request/response details to the active OpenTelemetry span.
"""

from __future__ import annotations

import os
from contextlib import nullcontext

from typing import Any, Callable, Mapping

from core.tracing import set_payload_attribute


def _http_success_body_logging_enabled() -> bool:
    """Return whether 2xx HTTP request/response payload capture is enabled.

    Env var: OTEL_HTTP_LOG_BODY (default: on)
    Truthy: 1/true/yes/on, Falsy: 0/false/no/off.
    """
    raw = os.getenv("OTEL_HTTP_LOG_BODY", "").strip().lower()
    if not raw:
        return True
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def get_current_recording_span() -> Any:
    """Return the active recording span, or None when unavailable."""
    try:
        from opentelemetry import trace
    except Exception:
        return None

    span = trace.get_current_span()
    return span if span and span.is_recording() else None


def enrich_http_span(
    resp: Any,
    *,
    provider: str,
    action: str,
    redact_url: Callable[[str], str] | None = None,
    extra_attributes: Mapping[str, Any] | None = None,
    include_payload_bodies: bool = True,
) -> Any:
    """Attach shared HTTP attributes and payloads to the active span."""
    span = get_current_recording_span()
    if span is None:
        return None

    request = getattr(resp, "request", None)
    raw_url = getattr(request, "url", "") or ""
    safe_url = redact_url(raw_url) if (redact_url and raw_url) else raw_url

    try:
        span.set_attribute(f"{provider}.action", action)
        span.set_attribute("http.status_code", int(getattr(resp, "status_code", 0) or 0))
        if safe_url:
            span.set_attribute("http.url", safe_url)
        if extra_attributes:
            for key, value in extra_attributes.items():
                if value is None:
                    continue
                span.set_attribute(key, value)
    except Exception:
        pass

    if include_payload_bodies:
        request_body = getattr(request, "body", None)
        if request_body:
            set_payload_attribute(span, "input.value", request_body)

        response_text = getattr(resp, "text", None)
        if response_text:
            set_payload_attribute(span, "output.value", response_text)

    return span


def mark_http_span_error(
    span: Any,
    *,
    provider: str,
    action: str,
    status_code: int,
    detail: str,
    response_body: str = "",
    error_messages: Any = None,
    field_errors: Mapping[str, Any] | None = None,
    extra_payloads: Mapping[str, Any] | None = None,
) -> None:
    """Set ERROR span status and provider-scoped error attributes."""
    if span is None:
        return

    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, f"{action}: {str(detail)[:300]}"))
        span.set_attribute(f"{provider}.error.detail", str(detail)[:2000])
        span.set_attribute(f"{provider}.error.status_code", int(status_code))

        if error_messages:
            set_payload_attribute(span, f"{provider}.error.messages", error_messages)
        if field_errors:
            set_payload_attribute(span, f"{provider}.error.fields", field_errors)
        if response_body:
            set_payload_attribute(span, f"{provider}.error.response_body", response_body)
        if extra_payloads:
            for key, value in extra_payloads.items():
                if value is None:
                    continue
                set_payload_attribute(span, key, value)
    except Exception:
        pass


def instrument_http_response(
    resp: Any,
    *,
    provider: str,
    action: str,
    redact_url: Callable[[str], str] | None = None,
    detail: str | None = None,
    error_messages: Any = None,
    field_errors: Mapping[str, Any] | None = None,
    extra_attributes: Mapping[str, Any] | None = None,
    extra_error_payloads: Mapping[str, Any] | None = None,
) -> tuple[Any, str | None]:
    """Enrich active span and mark error details for non-2xx responses.

    Returns ``(span, detail)`` where ``detail`` is non-empty for non-2xx paths.
    """
    is_ok = bool(getattr(resp, "ok", False))
    include_payload_bodies = (not is_ok) or _http_success_body_logging_enabled()

    parent_span = get_current_recording_span()
    span_context = nullcontext(None)
    span_from_context = None

    # Emit a dedicated integration span only when a parent recording span
    # exists, so this span is always a child and never an independent root.
    try:
        from opentelemetry import trace

        if parent_span is not None:
            tracer = trace.get_tracer("product-discovery.http")
            parent_ctx = trace.set_span_in_context(parent_span)
            span_context = tracer.start_as_current_span(
                f"integration.http.{provider}.{action}",
                context=parent_ctx,
            )
    except Exception:
        span_context = nullcontext(None)

    with span_context as started_span:
        if started_span is not None:
            span_from_context = started_span
            try:
                span_from_context.set_attribute("integration.http.span_source", "manual")
            except Exception:
                pass

        span = enrich_http_span(
            resp,
            provider=provider,
            action=action,
            redact_url=redact_url,
            extra_attributes=extra_attributes,
            include_payload_bodies=include_payload_bodies,
        )
        if span is None:
            span = span_from_context

        if is_ok:
            return span, None

        response_text = getattr(resp, "text", "") or ""
        error_detail = detail or (response_text[:200] if response_text else getattr(resp, "reason", "HTTP error"))

        mark_http_span_error(
            span,
            provider=provider,
            action=action,
            status_code=int(getattr(resp, "status_code", 0) or 0),
            detail=str(error_detail),
            response_body=response_text,
            error_messages=error_messages,
            field_errors=field_errors,
            extra_payloads=extra_error_payloads,
        )
        return span, str(error_detail)
