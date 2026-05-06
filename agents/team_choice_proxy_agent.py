"""Custom proxy agent for team_choice turns with multimodal payload support."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import uuid
from typing import Sequence

from autogen_core import CancellationToken
from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    HandoffMessage,
    MultiModalMessage,
    TextMessage,
    UserInputRequestedEvent,
)

from agents.session_coordination import (
    SessionCoordinationError,
    clear_team_choice_active_request,
    set_team_choice_active_request,
    wait_for_team_choice_response,
)
from core.tracing import set_payload_attribute, traced_block

logger = logging.getLogger(__name__)


class TeamChoiceProxyAgent(UserProxyAgent):
    """User proxy that waits for remote input and emits text or multimodal messages."""

    def __init__(
        self,
        name: str,
        *,
        session_id: str,
        remote_user_name: str,
        description: str = "Remote participant",
        round_number: int | None = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self._session_id = session_id
        self._remote_user_name = remote_user_name
        self._round_number = round_number

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage, MultiModalMessage, HandoffMessage)

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ):
        del messages  # UserProxy behavior depends on turn request, not message content.
        del cancellation_token
        # Keep explicit event parity with UserProxyAgent.
        request_id = str(uuid.uuid4())
        payload = None
        with traced_block(
            "agents.proxy.team_choice_turn",
            {
                "session_id": self._session_id,
                "proxy_name": self.name,
                "remote_user_name": self._remote_user_name,
                "round": int(self._round_number or 0),
                "request_id": request_id,
            },
        ) as span:
            set_payload_attribute(
                span,
                "input.value",
                {
                    "request_id": request_id,
                    "proxy_name": self.name,
                    "remote_user_name": self._remote_user_name,
                    "round": self._round_number,
                },
            )
            yield UserInputRequestedEvent(request_id=request_id, source=self.name)

            try:
                await asyncio.to_thread(
                    set_team_choice_active_request,
                    self._session_id,
                    request_id,
                    self.name,
                    self._remote_user_name,
                    self._round_number,
                )
                payload = await asyncio.to_thread(
                    wait_for_team_choice_response,
                    self._session_id,
                    request_id,
                )
            except SessionCoordinationError:
                logger.exception(
                    "agents.proxy.turn_request_failed",
                    extra={"session_id": self._session_id, "proxy": self.name},
                )
                payload = None
            finally:
                try:
                    await asyncio.to_thread(
                        clear_team_choice_active_request,
                        self._session_id,
                        request_id,
                    )
                except SessionCoordinationError:
                    logger.exception(
                        "agents.proxy.turn_request_cleanup_failed",
                        extra={"session_id": self._session_id, "proxy": self.name},
                    )

            set_payload_attribute(
                span,
                "output.value",
                {
                    "request_id": request_id,
                    "has_payload": bool(payload),
                    "responder_name": str((payload or {}).get("responder_name") or ""),
                    "text_len": len(str((payload or {}).get("text") or "")),
                    "attachment_count": len((payload or {}).get("attachment_ids") or []),
                    "image_count": len((payload or {}).get("images") or []),
                },
            )

        if not payload:
            yield Response(chat_message=TextMessage(content="Continue.", source=self.name))
            return

        text = (payload.get("text_with_context") or payload.get("text") or "").strip()
        images = payload.get("images") or []
        if images:
            mm_message = self._build_multimodal_message(self.name, text, images)
            if mm_message is not None:
                yield Response(chat_message=mm_message)
                return

        if payload.get("attachment_ids") and not text:
            text = "Attached files provided."
        if not text:
            text = "Continue."
        yield Response(chat_message=TextMessage(content=text, source=self.name))

    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        async for item in self.on_messages_stream(messages, cancellation_token):
            if isinstance(item, Response):
                return item
        raise AssertionError("The stream should have returned the final result.")

    @staticmethod
    def _build_multimodal_message(source: str, text: str, images_payload: list[dict]) -> MultiModalMessage | None:
        try:
            import PIL.Image
            from autogen_core import Image as AutoGenImage
        except Exception:
            return None

        content: list = [text or ""]
        for item in images_payload:
            try:
                raw_b64 = str((item or {}).get("data_b64") or "")
                if not raw_b64:
                    continue
                raw = base64.b64decode(raw_b64)
                pil_img = PIL.Image.open(io.BytesIO(raw))
                content.append(AutoGenImage(pil_img))
            except Exception:
                continue

        if len(content) <= 1:
            return None
        return MultiModalMessage(content=content, source=source)
