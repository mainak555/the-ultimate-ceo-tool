"""
Pure Trello REST API client — no Django imports.

Every function takes (api_key, token) plus endpoint-specific params.
All responses are simplified dicts; errors raise ValueError.

Per-call HTTP detail (timing, status, request/response bodies) is captured
on OpenTelemetry spans created by the auto-instrumented ``requests``
library; this module enriches the active span with the redacted action +
payload context. Only ERROR records are emitted to console.
"""

import logging
import re

import requests

from core.http_tracing import instrument_http_response

logger = logging.getLogger(__name__)

TRELLO_API = "https://api.trello.com/1"
LABEL_COLORS = ["green", "yellow", "orange", "red", "purple", "blue", "sky", "lime", "pink", "black"]

_SECRET_PARAM_RE = re.compile(r"(?i)([?&])(key|token)=[^&]*")


def _redact_url(url: str) -> str:
    """Strip Trello `key=` and `token=` query parameters before logging."""
    if not url:
        return url
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}=***", url)


def _auth_params(api_key, token):
    """Return the common auth query parameters."""
    return {"key": api_key, "token": token}


def _handle_api_response(resp, action):
    """Enrich the active span and raise ValueError on non-2xx responses."""
    _, detail = instrument_http_response(
        resp,
        provider="trello",
        action=action,
        redact_url=_redact_url,
    )

    if resp.ok:
        return

    method = getattr(resp.request, "method", "?") if resp.request else "?"
    url = _redact_url(getattr(resp.request, "url", "") or "")
    elapsed_ms = int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else 0

    detail = detail or (resp.text[:200] if resp.text else resp.reason)
    logger.error(
        "trello.api.error",
        extra={
            "action": action,
            "method": method,
            "url": url,
            "status": resp.status_code,
            "elapsed_ms": elapsed_ms,
            "body_snippet": (resp.text or "")[:500],
        },
    )
    raise ValueError(f"Trello API error ({action}): {resp.status_code} — {detail}")


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_workspaces(api_key, token):
    """GET /1/members/me/organizations → [{id, displayName}]"""
    resp = requests.get(
        f"{TRELLO_API}/members/me/organizations",
        params={**_auth_params(api_key, token), "fields": "id,displayName"},
        timeout=15,
    )
    _handle_api_response(resp, "get_workspaces")
    return [{"id": w["id"], "displayName": w.get("displayName", "")} for w in resp.json()]


def get_boards(api_key, token, workspace_id=None):
    """
    GET boards for a workspace or for the authenticated member.

    Returns [{id, name, closed}] — only open boards.
    """
    if workspace_id:
        url = f"{TRELLO_API}/organizations/{workspace_id}/boards"
    else:
        url = f"{TRELLO_API}/members/me/boards"
    resp = requests.get(
        url,
        params={**_auth_params(api_key, token), "fields": "id,name,closed", "filter": "open"},
        timeout=15,
    )
    _handle_api_response(resp, "get_boards")
    return [{"id": b["id"], "name": b.get("name", "")} for b in resp.json() if not b.get("closed")]


def get_lists(api_key, token, board_id):
    """GET /1/boards/<board_id>/lists → [{id, name}] — only open lists."""
    resp = requests.get(
        f"{TRELLO_API}/boards/{board_id}/lists",
        params={**_auth_params(api_key, token), "filter": "open", "fields": "id,name"},
        timeout=15,
    )
    _handle_api_response(resp, "get_lists")
    return [{"id": l["id"], "name": l.get("name", "")} for l in resp.json()]


# ---------------------------------------------------------------------------
# Create operations
# ---------------------------------------------------------------------------

def create_board(api_key, token, name, workspace_id=None):
    """POST /1/boards/ → {id, name}"""
    params = {**_auth_params(api_key, token), "name": name, "defaultLists": "false"}
    if workspace_id:
        params["idOrganization"] = workspace_id
    resp = requests.post(f"{TRELLO_API}/boards/", params=params, timeout=15)
    _handle_api_response(resp, "create_board")
    data = resp.json()
    return {"id": data["id"], "name": data.get("name", name)}


def create_list(api_key, token, name, board_id):
    """POST /1/lists → {id, name}"""
    params = {**_auth_params(api_key, token), "name": name, "idBoard": board_id}
    resp = requests.post(f"{TRELLO_API}/lists", params=params, timeout=15)
    _handle_api_response(resp, "create_list")
    data = resp.json()
    return {"id": data["id"], "name": data.get("name", name)}


def _get_list_board(api_key, token, list_id):
    """Return board id for a Trello list."""
    resp = requests.get(
        f"{TRELLO_API}/lists/{list_id}",
        params={**_auth_params(api_key, token), "fields": "id,idBoard,name"},
        timeout=15,
    )
    _handle_api_response(resp, "get_list")
    data = resp.json()
    board_id = data.get("idBoard")
    if not board_id:
        raise ValueError("Unable to resolve board for selected Trello list.")
    return board_id


def _get_board_labels(api_key, token, board_id):
    """Return labels for a board."""
    resp = requests.get(
        f"{TRELLO_API}/boards/{board_id}/labels",
        params={**_auth_params(api_key, token), "fields": "id,name,color", "limit": 1000},
        timeout=15,
    )
    _handle_api_response(resp, "get_board_labels")
    return resp.json()


def _create_label(api_key, token, board_id, name, color):
    """Create a board label."""
    resp = requests.post(
        f"{TRELLO_API}/labels",
        params={**_auth_params(api_key, token), "idBoard": board_id, "name": name, "color": color},
        timeout=15,
    )
    _handle_api_response(resp, "create_label")
    return resp.json()


def _attach_label(api_key, token, card_id, label_id):
    """Attach label to card."""
    resp = requests.post(
        f"{TRELLO_API}/cards/{card_id}/idLabels",
        params={**_auth_params(api_key, token), "value": label_id},
        timeout=15,
    )
    _handle_api_response(resp, "attach_label")


def _get_board_custom_fields(api_key, token, board_id):
    """Return custom field definitions for a board."""
    resp = requests.get(
        f"{TRELLO_API}/boards/{board_id}/customFields",
        params={**_auth_params(api_key, token)},
        timeout=15,
    )
    _handle_api_response(resp, "get_board_custom_fields")
    return resp.json()


def _create_text_custom_field(api_key, token, board_id, field_name):
    """Create a text custom field on a board."""
    resp = requests.post(
        f"{TRELLO_API}/customFields",
        params={
            **_auth_params(api_key, token),
            "idModel": board_id,
            "modelType": "board",
            "name": field_name,
            "type": "text",
        },
        timeout=15,
    )
    _handle_api_response(resp, "create_custom_field")
    return resp.json()


def _set_card_custom_field_text(api_key, token, card_id, custom_field_id, value):
    """Set card custom field text value."""
    resp = requests.put(
        f"{TRELLO_API}/cards/{card_id}/customField/{custom_field_id}/item",
        params={**_auth_params(api_key, token)},
        json={"value": {"text": value}},
        timeout=15,
    )
    _handle_api_response(resp, "set_custom_field")


# ---------------------------------------------------------------------------
# Export — push cards with checklists
# ---------------------------------------------------------------------------

def push_cards(api_key, token, list_id, items):
    """
    Create cards on the given list with checklists, labels, and custom fields.

    items — [{card_title, card_description, checklists, custom_fields, labels, priority, confidence_score}]

    Returns [{card_id, title, url, checklist_items?, labels?, warnings?}].
    """
    results = []
    auth = _auth_params(api_key, token)
    board_id = _get_list_board(api_key, token, list_id)

    labels_by_name = {}
    for row in _get_board_labels(api_key, token, board_id):
        label_name = (row.get("name") or "").strip().lower()
        if label_name and row.get("id"):
            labels_by_name[label_name] = row["id"]

    custom_fields_by_name = {}
    try:
        for field in _get_board_custom_fields(api_key, token, board_id):
            if not isinstance(field, dict):
                continue
            field_name = (field.get("name") or "").strip().lower()
            if field_name and field.get("id"):
                custom_fields_by_name[field_name] = field["id"]
    except ValueError:
        # Board may not support custom fields on current plan/permissions.
        custom_fields_by_name = {}

    for index, item in enumerate(items):
        warnings = []
        card_title = item.get("card_title") or item.get("title") or "Untitled"
        card_description = item.get("card_description") or item.get("description") or ""

        # Create card
        card_resp = requests.post(
            f"{TRELLO_API}/cards",
            params={
                **auth,
                "idList": list_id,
                "name": card_title,
                "desc": card_description,
            },
            timeout=15,
        )
        _handle_api_response(card_resp, "create_card")
        card = card_resp.json()

        result = {
            "card_id": card["id"],
            "title": card.get("name", ""),
            "url": card.get("shortUrl", ""),
        }

        # Create checklists if present.
        checklists = item.get("checklists") or []
        checklist_items = []
        for checklist in checklists:
            if not isinstance(checklist, dict):
                continue
            checklist_name = str(checklist.get("name") or "Tasks").strip() or "Tasks"
            cl_resp = requests.post(
                f"{TRELLO_API}/checklists",
                params={**auth, "idCard": card["id"], "name": checklist_name},
                timeout=15,
            )
            _handle_api_response(cl_resp, "create_checklist")
            checklist_id = cl_resp.json()["id"]
            for child in checklist.get("items") or []:
                if not isinstance(child, dict):
                    continue
                child_title = str(child.get("title") or "").strip()
                if not child_title:
                    continue
                ci_resp = requests.post(
                    f"{TRELLO_API}/checklists/{checklist_id}/checkItems",
                    params={**auth, "name": child_title},
                    timeout=15,
                )
                _handle_api_response(ci_resp, "create_checkItem")
                checklist_items.append(ci_resp.json().get("name", ""))
        if checklist_items:
            result["checklist_items"] = checklist_items

        # Apply labels (schema labels + derived priority label).
        all_labels = []
        for label_name in item.get("labels") or []:
            if isinstance(label_name, str) and label_name.strip():
                all_labels.append(label_name.strip())
        priority = str(item.get("priority") or "").strip()
        if priority:
            all_labels.append(f"Priority: {priority}")

        applied_labels = []
        for label_name in all_labels:
            key = label_name.lower()
            label_id = labels_by_name.get(key)
            if not label_id:
                color = LABEL_COLORS[(len(labels_by_name) + index) % len(LABEL_COLORS)]
                try:
                    created = _create_label(api_key, token, board_id, label_name, color)
                    label_id = created.get("id")
                    if label_id:
                        labels_by_name[key] = label_id
                except ValueError as exc:
                    warnings.append(f"Label '{label_name}' skipped: {exc}")
                    continue
            try:
                _attach_label(api_key, token, card["id"], label_id)
                applied_labels.append(label_name)
            except ValueError as exc:
                warnings.append(f"Label '{label_name}' not attached: {exc}")
        if applied_labels:
            result["labels"] = applied_labels

        # Apply custom fields (provided fields + normalized priority/confidence values).
        custom_fields = []
        for field in item.get("custom_fields") or []:
            if isinstance(field, dict):
                custom_fields.append(field)
        if priority:
            custom_fields.append({"field_name": "Priority", "field_type": "text", "value": priority})
        confidence = item.get("confidence_score")
        if confidence is not None:
            custom_fields.append(
                {
                    "field_name": "Confidence Score",
                    "field_type": "text",
                    "value": str(confidence),
                }
            )

        seen_custom_fields = set()
        for field in custom_fields:
            field_name = str(field.get("field_name") or "").strip()
            if not field_name:
                continue
            key = field_name.lower()
            if key in seen_custom_fields:
                continue
            seen_custom_fields.add(key)
            value = str(field.get("value") or "").strip()
            if value == "":
                continue

            custom_field_id = custom_fields_by_name.get(key)
            if not custom_field_id:
                try:
                    created = _create_text_custom_field(api_key, token, board_id, field_name)
                    custom_field_id = created.get("id")
                    if custom_field_id:
                        custom_fields_by_name[key] = custom_field_id
                except ValueError as exc:
                    warnings.append(f"Custom field '{field_name}' skipped: {exc}")
                    continue

            try:
                _set_card_custom_field_text(api_key, token, card["id"], custom_field_id, value)
            except ValueError as exc:
                warnings.append(f"Custom field '{field_name}' not set: {exc}")

        if warnings:
            result["warnings"] = warnings

        results.append(result)

    return results
