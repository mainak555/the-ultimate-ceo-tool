import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ServerConfig(AppConfig):
    name = "server"

    def ready(self):
        """One-shot startup hooks: tracing init and stale-session reset."""
        # Initialize Langfuse OpenTelemetry tracing for AutoGen (env-gated).
        try:
            from agents.tracing import init_tracing
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
