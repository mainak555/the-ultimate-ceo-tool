import logging
from datetime import datetime, timezone
from uuid import uuid4

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.template.loader import render_to_string

from core.tracing import traced_block
from . import attachment_service, services, views

logger = logging.getLogger(__name__)


def _group_name(session_id: str) -> str:
    return f"remote_session_{session_id}"


class RemoteUserConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket transport for remote-user page live updates.

    Source of truth remains in Redis + MongoDB service layer.
    Channel layer group fanout is used for advisory live updates.
    """

    session_id: str
    token: str
    user_id: str
    remote_user: dict

    async def connect(self):
        kwargs = (self.scope.get("url_route") or {}).get("kwargs") or {}
        self.session_id = str(kwargs.get("session_id") or "").strip()
        self.token = str(kwargs.get("token") or "").strip()
        with traced_block("ws.remote_user.connect", {"session_id": self.session_id}):
            if not self.session_id or not self.token:
                await self.close(code=4401)
                return

            self.user_id = await sync_to_async(self._resolve_user_id, thread_sensitive=True)()
            if not self.user_id:
                await self.close(code=4403)
                return

            session = await sync_to_async(services.get_chat_session, thread_sensitive=True)(self.session_id)
            if session is None:
                await self.close(code=4404)
                return
            project = await sync_to_async(services.get_project, thread_sensitive=True)(session.get("project_id", ""))
            if project is None:
                await self.close(code=4404)
                return

            remote_user = await sync_to_async(services.get_remote_user, thread_sensitive=True)(project, self.user_id)
            if not remote_user:
                await self.close(code=4403)
                return

            self.remote_user = remote_user

            await self.channel_layer.group_add(_group_name(self.session_id), self.channel_name)
            await self.accept()

            await sync_to_async(self._mark_online, thread_sensitive=True)()
            logger.info(
                "agents.remote_user.websocket_connected",
                extra={"session_id": self.session_id, "user_id": self.user_id},
            )
            await self._send_state()

    async def disconnect(self, code):
        if getattr(self, "session_id", ""):
            await self.channel_layer.group_discard(_group_name(self.session_id), self.channel_name)
            logger.info(
                "agents.remote_user.websocket_disconnected",
                extra={
                    "session_id": self.session_id,
                    "user_id": getattr(self, "user_id", ""),
                    "code": int(code or 0),
                },
            )

    async def receive_json(self, content, **kwargs):
        msg_type = str((content or {}).get("type") or "").strip().lower()
        logger.debug(
            "agents.remote_user.websocket_message_received",
            extra={
                "session_id": getattr(self, "session_id", ""),
                "user_id": getattr(self, "user_id", ""),
                "message_type": msg_type,
            },
        )
        if msg_type == "heartbeat":
            await sync_to_async(self._mark_online, thread_sensitive=True)()
            await self._send_state()
            return
        if msg_type == "sync_state":
            await self._send_state()
            return
        if msg_type == "submit_reply":
            await self._handle_submit_reply(content or {})
            return
        await self.send_json({"type": "error", "error": "Unsupported message type."})

    async def remote_state(self, event):
        await self._send_state()

    async def _handle_submit_reply(self, content: dict):
        with traced_block(
            "ws.remote_user.submit_reply",
            {"session_id": self.session_id, "user_id": self.user_id},
        ):
            session = await sync_to_async(services.get_chat_session, thread_sensitive=True)(self.session_id)
            if session is None:
                await self.send_json({"type": "error", "error": "Session not found."})
                return

            project = await sync_to_async(services.get_project, thread_sensitive=True)(session.get("project_id", ""))
            if project is None:
                await self.send_json({"type": "error", "error": "Project not found."})
                return

            if session.get("status") != "awaiting_input":
                await self.send_json({"type": "error", "error": "Session is not awaiting input."})
                await self._send_state()
                return

            turn_state = await sync_to_async(services.compute_remote_turn_state, thread_sensitive=True)(
                project,
                session,
                self.user_id,
            )
            if not turn_state.get("can_send"):
                await self.send_json({"type": "error", "error": "It is not your turn to respond."})
                await self._send_state()
                return

            text = str(content.get("text") or "").strip()
            attachment_ids = [str(x).strip() for x in (content.get("attachment_ids") or []) if str(x).strip()]
            if not text and not attachment_ids:
                await self.send_json({"type": "error", "error": "Message or attachment is required."})
                return

            message_id = str(uuid4())
            bound_attachments = await sync_to_async(
                attachment_service.bind_attachments_to_message,
                thread_sensitive=True,
            )(
                session_id=self.session_id,
                message_id=message_id,
                attachment_ids=attachment_ids,
            )
            display_attachments = await sync_to_async(
                views._enrich_attachments_for_display,
                thread_sensitive=True,
            )(self.session_id, bound_attachments)

            await sync_to_async(services.append_messages, thread_sensitive=True)(
                self.session_id,
                [
                    {
                        "id": message_id,
                        "agent_name": self.remote_user.get("name") or "Remote User",
                        "role": "user",
                        "content": text,
                        "attachments": display_attachments,
                        "timestamp": datetime.now(timezone.utc),
                    }
                ],
            )

            payload = {
                "user_id": self.user_id,
                "name": self.remote_user.get("name") or "Remote User",
                "text": text,
                "attachment_ids": [a.get("id") for a in display_attachments if a.get("id")],
            }
            await sync_to_async(self._record_gate_response, thread_sensitive=True)(
                int(session.get("current_round") or 0),
                views._json_dumps(payload),
            )
            await sync_to_async(self._mark_online, thread_sensitive=True)()

            logger.info(
                "agents.remote_user.reply_recorded",
                extra={
                    "session_id": self.session_id,
                    "user_id": self.user_id,
                    "round": int(session.get("current_round") or 0),
                    "has_text": bool(text),
                    "attachment_count": len(payload.get("attachment_ids") or []),
                },
            )

            await self.send_json({"type": "ack", "status": "ok"})
            await self.channel_layer.group_send(_group_name(self.session_id), {"type": "remote.state"})

    async def _send_state(self):
        with traced_block(
            "ws.remote_user.send_state",
            {"session_id": self.session_id, "user_id": self.user_id},
        ):
            session = await sync_to_async(services.get_chat_session, thread_sensitive=True)(self.session_id)
            if session is None:
                await self.send_json({"type": "state", "status": "error", "error": "Session not found"})
                return
            project = await sync_to_async(services.get_project, thread_sensitive=True)(session.get("project_id", ""))
            if project is None:
                await self.send_json({"type": "state", "status": "error", "error": "Project not found"})
                return

            export_meta = await sync_to_async(views._build_export_meta, thread_sensitive=True)(project)
            history_messages = await sync_to_async(views._build_history_messages, thread_sensitive=True)(session, export_meta)
            history_html = await sync_to_async(render_to_string, thread_sensitive=True)(
                "server/partials/chat_session_history.html",
                {
                    "session": session,
                    "project": project,
                    "history_export_meta": export_meta,
                    "history_messages": history_messages,
                    "history_viewer_name": self.remote_user.get("name") or "",
                },
            )

            turn_state = await sync_to_async(services.compute_remote_turn_state, thread_sensitive=True)(
                project,
                session,
                self.user_id,
            )
            leader_online = await sync_to_async(self._is_leader_online, thread_sensitive=True)()
            participants = [
                {
                    "name": "Leader",
                    "online": bool(leader_online),
                    "active": bool(session.get("status") == "awaiting_input"),
                }
            ]
            for row in turn_state.get("participants") or []:
                if row.get("user_id") == self.user_id:
                    continue
                participants.append(
                    {
                        "name": row.get("name") or row.get("user_id") or "User",
                        "online": bool(row.get("online")),
                        "active": bool(row.get("active")),
                    }
                )

            await self.send_json(
                {
                    "type": "state",
                    "status": "ok",
                    "can_send": bool(turn_state.get("can_send")),
                    "participants": participants,
                    "history_html": history_html,
                    "session_status": session.get("status", "idle"),
                    "round": int(session.get("current_round") or 0),
                }
            )

    def _resolve_user_id(self) -> str | None:
        from agents.session_coordination import lookup_remote_user_token

        return lookup_remote_user_token(self.session_id, self.token)

    def _mark_online(self) -> None:
        from agents.session_coordination import set_remote_user_online

        set_remote_user_online(self.session_id, self.user_id)

    def _is_leader_online(self) -> bool:
        from agents.session_coordination import is_leader_online

        return is_leader_online(self.session_id)

    def _record_gate_response(self, round_no: int, payload_json: str) -> None:
        from agents.session_coordination import record_remote_gate_response

        record_remote_gate_response(
            self.session_id,
            round_no,
            self.user_id,
            payload_json,
        )
