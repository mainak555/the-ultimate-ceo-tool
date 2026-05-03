"""Shared server utility helpers.

This module hosts small, reusable, side-effect-light helpers used across
feature views/services to keep behavior consistent and avoid helper duplication.
"""

from datetime import datetime, timezone
import json

from django.http import HttpResponse


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime for BSON Date writes."""
    return datetime.now(timezone.utc)


def coerce_confidence(value):
    """Coerce input into a confidence score clamped to the range [0.0, 1.0]."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, out))


def normalize_labels(labels):
    """Return cleaned labels with case-insensitive dedupe while preserving order."""
    if not isinstance(labels, list):
        return []
    seen = set()
    out = []
    for lbl in labels:
        txt = str(lbl or "").strip()
        if txt and txt.lower() not in seen:
            seen.add(txt.lower())
            out.append(txt)
    return out


def json_response(data, status=200):
    """Build a JSON HttpResponse using shared datetime-aware serialization."""
    return HttpResponse(json_dumps(data), status=status, content_type="application/json")


def json_error(message, status=400):
    """Return a standard JSON error payload using the shared response helper."""
    return json_response({"error": message}, status=status)


def json_default(value):
    """Serialize datetime values for JSON boundaries.

    Naive datetimes are treated as UTC to preserve existing DB semantics.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(payload) -> str:
    """Serialize payloads to JSON with the module's shared default encoder."""
    return json.dumps(payload, default=json_default)

