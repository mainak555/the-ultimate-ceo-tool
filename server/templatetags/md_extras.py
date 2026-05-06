import json

import markdown as _md
from django import template
from django.utils.safestring import mark_safe

from server.util import QUORUM_OPTIONS

register = template.Library()


@register.filter(is_safe=True)
def markdownify(value):
    """Render a Markdown string to safe HTML."""
    if not value:
        return ""
    html = _md.markdown(
        str(value),
        extensions=["nl2br", "fenced_code", "tables"],
        output_format="html",
    )
    return mark_safe(html)


@register.filter(name="to_json")
def to_json(value):
    """Render a Python value as a pretty JSON string for display in textareas/code blocks."""
    if value in (None, "", {}, []):
        return ""
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


@register.filter(name="quorum_label")
def quorum_label(value):
    """Map quorum value to display label using util.QUORUM_OPTIONS."""
    key = (value or "").strip()
    if not key:
        return ""

    for opt in QUORUM_OPTIONS:
        if opt.get("value") == key:
            return opt.get("label") or key
    return key
