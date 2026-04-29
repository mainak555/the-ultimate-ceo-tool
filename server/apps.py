import logging
import atexit

from django.apps import AppConfig

logger = logging.getLogger(__name__)
_shutdown_hook_registered = False


def _shutdown_runtime_resources() -> None:
    """Best-effort process shutdown cleanup for runtime/MCP resources."""
    try:
        from agents.runtime import evict_all_teams
        evict_all_teams()
    except Exception:
        logger.exception("agents.shutdown_failed", extra={"phase": "evict_all_teams"})

    try:
        from agents.mcp_tools import close_all_workbenches
        close_all_workbenches()
    except Exception:
        logger.exception("agents.shutdown_failed", extra={"phase": "close_all_workbenches"})


class ServerConfig(AppConfig):
    name = "server"

    def ready(self):
        """One-shot startup hooks: tracing init and stale-session reset."""
        global _shutdown_hook_registered

        if not _shutdown_hook_registered:
            atexit.register(_shutdown_runtime_resources)
            _shutdown_hook_registered = True

        # Initialize OpenTelemetry tracing (env-gated). This wires the OTLP
        # exporter (currently Langfuse), Django/requests/pymongo auto-
        # instrumentation, and the AutoGen event-log -> span bridge.
        try:
            from core.tracing import init_tracing
            init_tracing()
        except Exception:
            logger.exception("tracing.init_failed")

        # Reset sessions stuck in 'running' state from a previous server process.
        from .db import get_collection, CHAT_SESSIONS_COLLECTION
        try:
            col = get_collection(CHAT_SESSIONS_COLLECTION)
            result = col.update_many({"status": "running"}, {"$set": {"status": "idle"}})
            if result.modified_count:
                logger.info(
                    "chat.session.reset_stuck",
                    extra={"reset_count": result.modified_count},
                )
        except Exception:
            logger.exception("chat.session.reset_stuck_failed")
