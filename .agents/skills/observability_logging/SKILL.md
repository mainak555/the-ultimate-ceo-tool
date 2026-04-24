---
name: observability-logging
description: Use when adding HTTP clients, MongoDB access, service-layer code, AutoGen runtime code, model client wrappers, or any module that performs I/O. Enforces structured JSON logging via module loggers, request-ID propagation, secret redaction, and Langfuse OpenTelemetry tracing for AutoGen agents.
---

# Skill: Observability & Logging

## Purpose
Single source of truth for application logging and AutoGen tracing. Every new HTTP client, service module, MongoDB call, and agent runtime entry point must conform to the rules below before merge.

## Mandatory Logger Rules
1. Every Python module that performs I/O declares `logger = logging.getLogger(__name__)` at module top. No custom logger names. No `print()` for diagnostics.
2. Logging configuration lives only in `config/settings.py` (`LOGGING` dict). One stderr `StreamHandler` + JSON formatter (`python-json-logger`). Root level driven by `LOG_LEVEL` env var (default `INFO`).
3. Event names use dotted snake_case scoped by layer:
   - Service layer: `mongo.connect`, `mongo.connect_failed`, `project.created`, `chat.session.started`.
   - HTTP clients: `trello.api.call`, `trello.api.error`, `jira.api.call`, `jira.api.error`.
   - Agent runtime: `agents.model_client.created`, `agents.team.built`, `agents.team.cancelled`, `agents.extraction.completed`, `agents.extraction.parse_failed`.
4. Use `logger.info` for successful lifecycle events with structured context (`extra={...}`). Use `logger.warning` immediately before raising a `ValueError` for an expected business-rule failure. Use `logger.exception` for unexpected exceptions.
5. Never log full request/response payloads. Log identifiers, counts, status codes, and `elapsed_ms`. Body snippets allowed only when needed for debugging (≤ 500 chars, sanitized).

## Mandatory Redaction Rules
1. Strip Trello `key=` and `token=` query parameters from any URL before logging it.
2. Never log the `Authorization` header, Basic-auth strings, API keys, OAuth tokens, passwords, or `X-App-Secret-Key`.
3. Never log raw user emails unless required for an active debug trace; prefer project key / issue key context.
4. Validate diffs with `grep -nE "API_KEY|SECRET|password|Authorization|Bearer" <changed files>` and ensure no matches sit inside log strings.

## Service Layer Events Catalog
| Module | Required Events |
|--------|------------------|
| `server/db.py` | `mongo.connect` (INFO, first connect), `mongo.connect_failed` (EXCEPTION) |
| `server/services.py` | `project.created`/`project.updated`/`project.deleted` (INFO with project_id), `chat.session.started`/`chat.session.ended` (INFO with session_id), validation `WARNING` before raising `ValueError` |
| `server/trello_service.py`, `server/jira_service.py`, `server/jira_*_service.py` | Credential resolution failures as `WARNING`; currently-swallowed `ValueError` fallbacks must call `logger.warning("...fallback", exc_info=True)` instead of silently passing |

## HTTP Client Wrapping Pattern
Both `server/trello_client.py` and `server/jira_client.py` wrap their request helper:

1. Capture `t0 = time.monotonic()` before the call.
2. On 2xx response: `logger.info("{provider}.api.call", extra={"method": method, "path": path, "status": resp.status_code, "elapsed_ms": int((time.monotonic()-t0)*1000)})`.
3. On non-2xx response: `logger.error("{provider}.api.error", extra={"method": method, "path": path, "status": resp.status_code, "body_snippet": resp.text[:500]})` then raise `ValueError` as today.
4. The logged `path` must be the URL with secrets stripped (Trello query params; never include the Authorization header).

## Agent Runtime Events Catalog
| Module | Required Events |
|--------|------------------|
| `agents/factory.py` | `agents.model_client.created` (INFO with `{provider, model_name}` — never the API key); `EXCEPTION` on import / construction failure |
| `agents/team_builder.py` | `agents.team.built` (INFO with `{team_type, agent_count, selector_used}`) |
| `agents/runtime.py` | Cache hit/miss at `DEBUG`; `agents.team.started`/`agents.team.cancelled` at `INFO` |
| `agents/integrations/extractor.py` | `agents.extraction.started`/`agents.extraction.completed` with `elapsed_ms`; `agents.extraction.parse_failed` via `logger.exception` with sanitized response snippet |

## Request ID Propagation
1. `server/middleware.py` defines `RequestIdMiddleware` and is registered at the **top** of `MIDDLEWARE` in `config/settings.py`.
2. The middleware reads incoming `X-Request-ID` header or generates `uuid4().hex[:12]`, binds it via `bind_request_id()` (a `contextvars.ContextVar` set in `server/logging_utils.py`), echoes the value back as a response header, and clears the binding in `finally`.
3. `RequestIdFilter` (in `server/logging_utils.py`) injects the current request id onto every `LogRecord` as `record.request_id`. Default value is `"-"` when no request is active.
4. Async tasks awaited within a request inherit the contextvar automatically. Do not pass request ids manually.

## Tracing (Langfuse via OpenTelemetry)
1. `agents/tracing.py` owns all tracing wiring. Nothing else imports OpenTelemetry directly.
2. `init_tracing()` reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (default `https://cloud.langfuse.com`). If any of the first two is missing, log `tracing.disabled` (INFO) and return — no exporter, no errors.
3. With keys present: build a Basic-auth header `base64(public:secret)`, configure an OTLP HTTP exporter to `{LANGFUSE_HOST}/api/public/otel/v1/traces`, attach a `BatchSpanProcessor` to a `TracerProvider`, then enable AutoGen instrumentation.
4. `init_tracing()` is invoked exactly once from `server/apps.py` `ServerConfig.ready()`. Idempotent across reloads.
5. Never log Langfuse keys. Never raise on tracing setup failure — log `EXCEPTION` and continue.

## Validation Checklist
1. `grep -RnE "print\(" server/ agents/` returns zero matches (excluding tests / docs).
2. Every new I/O function emits at least one INFO event on success and one ERROR/EXCEPTION event on failure.
3. `grep -nE "API_KEY|SECRET|password|Authorization|Bearer" <git-diff-files>` shows no occurrences inside log message strings.
4. `python manage.py runserver` starts cleanly; the first HTTP request emits a JSON log line containing `request_id`.
5. With `LANGFUSE_*` env vars unset, server starts and logs `tracing.disabled`. With them set, AutoGen runs produce traces in Langfuse.
6. New modules use `logging.getLogger(__name__)` — no string literals.
