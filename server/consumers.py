"""WebSocket consumers for real-time MCP OAuth readiness updates.

``OAuthReadinessConsumer`` subscribes to a Redis pub/sub channel for a given
session and pushes OAuth authorization events to the browser, replacing the
3-second polling loop that previously hit ``GET /mcp/oauth/check/<session_id>/``.

Protocol (JSON frames):

    {type: "state",  servers: [{name, authorized}], total: N}
        Sent once on connect — full initial state per server.

    {type: "update", server_name, authorized_count, total_count}
        Sent each time a server is newly authorized (pub/sub event from callback).

    {type: "complete"}
        Sent when authorized_count >= total_count.  Frontend resumes the run.

    {type: "error",  message}
        Sent on authentication failure or unexpected condition; closes the WS.

Authentication:
    ``?skey=<APP_SECRET_KEY>`` query param (WS connections cannot set headers).
    Requests with a missing or invalid secret are rejected before accepting.
"""

from __future__ import annotations

import asyncio
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_SENTINEL = object()  # sentinel to signal listen task to stop


class OAuthReadinessConsumer:
    """Async WebSocket consumer for MCP OAuth readiness.

    Intentionally *not* a channels ``JsonWebsocketConsumer`` subclass — we need
    a raw async context so we can run ``redis.asyncio`` inside the same event
    loop without blocking the thread pool.
    """

    # channels framework calls these class-level
    channel_layer = None  # no channel layer used
    channel_layer_alias = "default"

    def __init__(self, scope, receive, send):
        self.scope = scope
        self._receive = receive
        self._send = send
        self._session_id: str = ""
        self._listen_task: asyncio.Task | None = None
        self._closed = False

    @classmethod
    def as_asgi(cls):
        """Return a standard ASGI application factory for URLRouter."""
        async def app(scope, receive, send):
            consumer = cls(scope, receive, send)
            await consumer.__call__(scope, receive, send)
        return app

    async def __call__(self, scope, receive, send):
        # Django Channels passes (scope, receive, send); store for internal use.
        self._receive = receive
        self._send = send

        if scope["type"] != "websocket":
            return

        self._session_id = scope["url_route"]["kwargs"]["session_id"]
        qs = dict(
            pair.split("=", 1)
            for pair in (scope.get("query_string", b"").decode().split("&"))
            if "=" in pair
        )
        skey = qs.get("skey", "")
        app_secret = getattr(settings, "APP_SECRET_KEY", "") or ""

        if not app_secret or skey != app_secret:
            # Reject before accept: send close immediately.
            await send({"type": "websocket.close", "code": 4003})
            logger.warning(
                "agents.mcp.oauth_ws_auth_failed",
                extra={"session_id": self._session_id},
            )
            return

        # Accept the WebSocket handshake.
        await send({"type": "websocket.accept"})

        try:
            await self._handle(skey)
        except Exception:
            logger.exception(
                "agents.mcp.oauth_ws_error",
                extra={"session_id": self._session_id},
            )
        finally:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
                try:
                    await self._listen_task
                except (asyncio.CancelledError, Exception):
                    pass
            if not self._closed:
                self._closed = True
                try:
                    await send({"type": "websocket.close", "code": 1000})
                except Exception:
                    pass

    async def _handle(self, skey: str):
        import asyncio as _asyncio

        session_id = self._session_id

        # Load session + project in thread pool (sync DB calls).
        session, project_raw, server_names = await _asyncio.to_thread(
            self._load_session_data, session_id
        )
        if session is None:
            await self._send_json({"type": "error", "message": "Session not found."})
            return
        if project_raw is None:
            await self._send_json({"type": "error", "message": "Project not found."})
            return

        if not server_names:
            # No OAuth servers — nothing to wait for; close cleanly.
            await self._send_json({"type": "complete"})
            return

        total = len(server_names)

        # Compute per-server authorized state from Redis token keys.
        from agents.session_coordination import list_authorized_oauth_servers
        authorized_names = await _asyncio.to_thread(
            list_authorized_oauth_servers, session_id, server_names
        )
        authorized_set = set(authorized_names)

        initial_servers = [
            {"name": name, "authorized": name in authorized_set}
            for name in server_names
        ]
        await self._send_json({
            "type": "state",
            "servers": initial_servers,
            "total": total,
        })

        if len(authorized_set) >= total:
            await self._send_json({"type": "complete"})
            return

        # Start listening on Redis pub/sub in the background.
        self._listen_task = _asyncio.ensure_future(
            self._listen_redis(session_id, total)
        )

        # Drain incoming WS frames (clients may send pings; we ignore them).
        try:
            while not self._closed:
                msg = await self._receive()
                if msg["type"] == "websocket.disconnect":
                    self._closed = True
                    break
        finally:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()

    async def _listen_redis(self, session_id: str, total: int):
        """Subscribe to pub/sub channel and forward events to the WS client."""
        import redis.asyncio as aioredis
        from agents.session_coordination import _mcp_oauth_pubsub_channel  # type: ignore[attr-defined]

        channel = _mcp_oauth_pubsub_channel(session_id)
        redis_url = getattr(settings, "REDIS_URI", "redis://localhost:6379/0")

        try:
            async with aioredis.from_url(redis_url, decode_responses=True) as client:
                async with client.pubsub() as ps:
                    await ps.subscribe(channel)
                    logger.info(
                        "agents.mcp.oauth_ws_subscribed",
                        extra={"session_id": session_id, "channel": channel},
                    )
                    async for raw_msg in ps.listen():
                        if self._closed:
                            break
                        if raw_msg.get("type") != "message":
                            continue
                        try:
                            payload = json.loads(raw_msg["data"])
                        except (ValueError, KeyError):
                            continue

                        authorized_count = payload.get("authorized_count", 0)
                        await self._send_json({
                            "type": "update",
                            "server_name": payload.get("server_name", ""),
                            "authorized_count": authorized_count,
                            "total_count": payload.get("total_count", total),
                        })

                        if authorized_count >= total:
                            await self._send_json({"type": "complete"})
                            logger.info(
                                "agents.mcp.oauth_ws_complete",
                                extra={"session_id": session_id},
                            )
                            break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "agents.mcp.oauth_ws_listen_error",
                extra={"session_id": session_id},
            )

    async def _send_json(self, data: dict):
        try:
            await self._send({
                "type": "websocket.send",
                "text": json.dumps(data),
            })
        except Exception:
            pass  # client may have disconnected

    @staticmethod
    def _load_session_data(session_id: str):
        """Synchronous: load session + project and return OAuth server list."""
        from server import services

        session = services.get_chat_session(session_id)
        if session is None:
            return None, None, []

        project_raw = services.get_project_raw(session.get("project_id", ""))
        if project_raw is None:
            return session, None, []

        server_names = services.list_all_reachable_oauth_servers(project_raw)
        return session, project_raw, server_names
