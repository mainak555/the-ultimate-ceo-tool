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
            logger.error(
                "agents.mcp.oauth_ws_error",
                extra={"session_id": session_id, "reason": "session_not_found"},
            )
            await self._send_json({"type": "error", "message": "Session not found."})
            return
        if project_raw is None:
            logger.error(
                "agents.mcp.oauth_ws_error",
                extra={"session_id": session_id, "reason": "project_not_found"},
            )
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
                        extra={
                            "session_id": session_id,
                            "server_count": total,
                            "channel": channel,
                        },
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


# ---------------------------------------------------------------------------
# Remote-user readiness consumer — host browser watches remote users joining
# ---------------------------------------------------------------------------

class RemoteUserReadinessConsumer:
    """Async WebSocket consumer for remote-user join readiness.

    Protocol (JSON frames):

        {type: "state", users: [{name, status}], required_count, online_count}
            Sent once on connect.

        {type: "update", user_name, status, online_count, required_count}
            Sent each time a user's status changes.

        {type: "count_update", online_count, required_count}
            Sent on ignore/unignore to update counts without a status change.

        {type: "complete"}
            Sent when all required users are online/ignored.

        {type: "error", message}
            On authentication failure or unexpected condition.

    Authentication: ``?skey=<APP_SECRET_KEY>`` query param.
    """

    channel_layer = None
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
        async def app(scope, receive, send):
            consumer = cls(scope, receive, send)
            await consumer.__call__(scope, receive, send)
        return app

    async def __call__(self, scope, receive, send):
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
            await send({"type": "websocket.close", "code": 4003})
            logger.warning(
                "agents.remote_user.ws_auth_failed",
                extra={"session_id": self._session_id},
            )
            return

        await send({"type": "websocket.accept"})

        try:
            await self._handle()
        except Exception:
            logger.exception(
                "agents.remote_user.ws_error",
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

    async def _handle(self):
        import asyncio as _asyncio

        session_id = self._session_id

        session, project, participant_names, project_quorum = await _asyncio.to_thread(
            self._load_session_data, session_id
        )
        if session is None:
            await self._send_json({"type": "error", "message": "Session not found."})
            return
        if project is None:
            await self._send_json({"type": "error", "message": "Project not found."})
            return

        # Effective quorum: per-session Redis override takes precedence.
        from agents.session_coordination import get_remote_user_statuses, get_session_quorum
        effective_quorum = await _asyncio.to_thread(get_session_quorum, session_id) or project_quorum

        statuses = await _asyncio.to_thread(get_remote_user_statuses, session_id, participant_names)

        participant_ignored = sum(1 for n in participant_names if statuses.get(n) == "ignored")
        online_count = sum(1 for n in participant_names if statuses.get(n) == "online")
        required_count = len(participant_names) - participant_ignored

        users_state = [
            {"name": n, "status": statuses.get(n, "offline")}
            for n in participant_names
        ]

        await self._send_json({
            "type": "state",
            "users": users_state,
            "online_count": online_count,
            "required_count": required_count,
            "quorum": effective_quorum,
        })

        if not participant_names or online_count >= required_count:
            await self._send_json({"type": "complete"})
            return

        self._listen_task = _asyncio.ensure_future(
            self._listen_redis(session_id, participant_names)
        )

        try:
            while not self._closed:
                msg = await self._receive()
                if msg["type"] == "websocket.disconnect":
                    self._closed = True
                    break
        finally:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()

    async def _listen_redis(self, session_id: str, participant_names: list):
        """Listen for remote-user pub/sub events and forward state to the WebSocket."""
        import redis.asyncio as aioredis
        from agents.session_coordination import _remote_user_pubsub_channel  # type: ignore[attr-defined]

        channel = _remote_user_pubsub_channel(session_id)
        redis_url = getattr(settings, "REDIS_URI", "redis://localhost:6379/0")

        try:
            async with aioredis.from_url(redis_url, decode_responses=True) as client:
                async with client.pubsub() as ps:
                    await ps.subscribe(channel)
                    logger.info(
                        "agents.remote_user.ws_subscribed",
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

                        event_type = payload.get("type")
                        if event_type == "update":
                            updated_name = payload.get("user_name", "")
                            import asyncio as _asyncio
                            from agents.session_coordination import get_remote_user_statuses
                            statuses = await _asyncio.to_thread(
                                get_remote_user_statuses, session_id, participant_names
                            )
                            participant_ignored = sum(1 for s in statuses.values() if s == "ignored")
                            online_count = sum(1 for s in statuses.values() if s == "online")
                            required_count = len(participant_names) - participant_ignored
                            await self._send_json({
                                "type": "update",
                                "user_name": updated_name,
                                "status": payload.get("status", "offline"),
                                "online_count": online_count,
                                "required_count": required_count,
                            })
                            # Only complete when participant quorum is satisfied.
                            if required_count >= 0 and online_count >= required_count:
                                await self._send_json({"type": "complete"})
                                break
                        elif event_type == "count_update":
                            await self._send_json(payload)
                        elif event_type == "complete":
                            await self._send_json({"type": "complete"})
                            logger.info(
                                "agents.remote_user.ws_complete",
                                extra={"session_id": session_id},
                            )
                            break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "agents.remote_user.ws_listen_error",
                extra={"session_id": session_id},
            )

    async def _send_json(self, data: dict):
        try:
            await self._send({"type": "websocket.send", "text": json.dumps(data)})
        except Exception:
            pass

    @staticmethod
    def _load_session_data(session_id: str):
        from server import services
        session = services.get_chat_session(session_id)
        if session is None:
            return None, None, [], "na"
        project = services.get_project(session.get("project_id", ""))
        if project is None:
            return session, None, [], "na"
        remote_users_cfg = (project.get("human_gate") or {}).get("remote_users") or []
        participant_names = [r["name"] for r in remote_users_cfg if isinstance(r, dict) and r.get("name")]
        project_quorum = (project.get("human_gate") or {}).get("quorum", "na")
        return session, project, participant_names, project_quorum


# ---------------------------------------------------------------------------
# Remote-chat consumer — remote user receives live messages from agents
# ---------------------------------------------------------------------------

class RemoteChatConsumer:
    """Async WebSocket consumer for remote users watching agent conversations.

    Protocol (JSON frames):

        {type: "history", messages: [...]}
            Sent once on connect — full discussion history so far.

        {type: "message", message: {...}}
            Live agent message forwarded from the session message channel.

        {type: "evict"}
            Sent when the token is revoked (user ignored by host).
            Frontend should show an overlay and stop reconnecting.

        {type: "error", message}
            On token validation failure.

    Authentication: token in URL path (UUID4, Redis-backed TTL).
    No APP_SECRET_KEY required — this is the public remote-user page.
    """

    channel_layer = None
    channel_layer_alias = "default"

    def __init__(self, scope, receive, send):
        self.scope = scope
        self._receive = receive
        self._send = send
        self._token: str = ""
        self._listen_task: asyncio.Task | None = None
        self._closed = False

    @classmethod
    def as_asgi(cls):
        async def app(scope, receive, send):
            consumer = cls(scope, receive, send)
            await consumer.__call__(scope, receive, send)
        return app

    async def __call__(self, scope, receive, send):
        self._receive = receive
        self._send = send

        if scope["type"] != "websocket":
            return

        self._token = scope["url_route"]["kwargs"]["token"]

        await send({"type": "websocket.accept"})

        try:
            await self._handle()
        except Exception:
            logger.exception("agents.remote_user.chat_ws_error")
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

    async def _handle(self):
        import asyncio as _asyncio

        token = self._token
        token_data = await _asyncio.to_thread(self._validate_token, token)
        if token_data is None:
            await self._send_json({"type": "error", "message": "Invalid or expired invitation link."})
            return

        session_id = token_data["session_id"]
        user_name = token_data["user_name"]

        # Send full history.
        history = await _asyncio.to_thread(self._load_history, session_id)
        await self._send_json({"type": "history", "messages": history})

        self._listen_task = _asyncio.ensure_future(
            self._listen_redis(session_id, user_name, token)
        )

        try:
            while not self._closed:
                msg = await self._receive()
                if msg["type"] == "websocket.disconnect":
                    self._closed = True
                    break
        finally:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()

    async def _listen_redis(self, session_id: str, user_name: str, token: str):
        import redis.asyncio as aioredis
        from agents.session_coordination import (
            _session_message_channel,         # type: ignore[attr-defined]
            _remote_user_pubsub_channel,       # type: ignore[attr-defined]
        )

        msg_channel = _session_message_channel(session_id)
        readiness_channel = _remote_user_pubsub_channel(session_id)
        redis_url = getattr(settings, "REDIS_URI", "redis://localhost:6379/0")

        try:
            async with aioredis.from_url(redis_url, decode_responses=True) as client:
                async with client.pubsub() as ps:
                    await ps.subscribe(msg_channel, readiness_channel)
                    async for raw_msg in ps.listen():
                        if self._closed:
                            break
                        if raw_msg.get("type") != "message":
                            continue
                        try:
                            payload = json.loads(raw_msg["data"])
                        except (ValueError, KeyError):
                            continue

                        raw_channel = raw_msg.get("channel", "")

                        if raw_channel == msg_channel:
                            # Forward agent message to the remote user.
                            await self._send_json(payload)
                        elif raw_channel == readiness_channel:
                            # Detect eviction: user's token was revoked (ignored).
                            if payload.get("type") == "update" and payload.get("user_name") == user_name:
                                if payload.get("status") == "ignored":
                                    await self._send_json({"type": "evict"})
                                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "agents.remote_user.chat_ws_listen_error",
                extra={"session_id": session_id},
            )

    async def _send_json(self, data: dict):
        try:
            await self._send({"type": "websocket.send", "text": json.dumps(data)})
        except Exception:
            pass

    @staticmethod
    def _validate_token(token: str) -> dict | None:
        try:
            from agents.session_coordination import get_remote_user_token_data
            return get_remote_user_token_data(token)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _load_history(session_id: str) -> list:
        from server import services
        session = services.get_chat_session(session_id)
        if session is None:
            return []
        discussions = session.get("discussions") or []
        result = []
        for d in discussions:
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp", "")
            result.append({
                "id": str(d.get("id", "")),
                "agent_name": d.get("agent_name", ""),
                "role": d.get("role", ""),
                "content": d.get("content", ""),
                "timestamp": ts if isinstance(ts, str) else (ts.isoformat() if hasattr(ts, "isoformat") else ""),
            })
        return result
