"""One authenticated voice WebSocket: session, Live bridge, turns, persistence, limits.

``VoiceConnection.run`` is called after the handshake in ``app/api/v1/voice.py`` authenticated the
caller. It resolves the conversation (an existing chat session or a new one), connects Gemini
Live, reports ``READY``/``LIVE_UNAVAILABLE``, then supervises the loops until the client leaves,
a limit trips or a loop dies. A dead loop always ends in an ``ERROR`` frame and a close code.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.auth.models import Principal
from app.mail.tools import MAIL_READ_NAMES, MailToolkit, mail_voice_declarations
from app.mcp.notion_tools import NOTION_TOOL_NAMES, notion_voice_declarations
from app.mcp.show_code import SHOW_CODE_NAME, show_code_voice_declaration
from app.mcp_client import service as mcp_runtime
from app.mcp_client import voice as mcp_voice
from app.mcp_client.service import Identity as McpIdentity
from app.schedules import runtime as schedule_runtime
from app.schedules.factory import ScheduleServices
from app.schedules.tools import (
    VOICE_SCHEDULE_TOOL_NAMES,
    ScheduleToolkit,
    schedule_voice_declarations,
)
from app.schemas.chat import InputRequestedEvent, ScheduleDraftEvent
from app.services import ask_user, required_details
from app.services.image_gen import ImageError
from app.voice import protocol
from app.voice.event_bridge import VoiceEventBridge
from app.voice.gemini_live import Connector, GeminiLiveBridge, LiveUnavailable
from app.voice.live_session import LiveVoiceSession, OutgoingMessageType
from app.voice.memory import (
    SavedMemory,
    VoiceMemory,
    VoiceMemoryTools,
    connect_memory_block,
    memory_enabled,
)
from app.voice.notion_voice import (
    SAVE_EXISTING,
    VOICE_NOTION_WRITE_NAMES,
    database_names,
    notion_write_voice_declarations,
    propose_by_voice,
    save_existing_by_voice,
    save_existing_declarations,
)
from app.voice.persistence import (
    SessionNotOwned,
    VoicePersistenceError,
    VoicePersistenceService,
)
from app.voice.prompts import build_voice_instruction
from app.voice.schedule_digest import voice_updates
from app.voice.session_manager import VoiceSessionManager
from app.voice.task_watch import (
    SPACING_SECONDS,
    AnnouncedStore,
    TaskAnnouncer,
    find_catch_up,
    make_db_resolver,
    make_review_store,
)
from app.voice.task_wiring import wire_voice_tools
from app.voice.tools import SHOW_IN_CHAT, VoiceExecutionContext, VoiceToolRegistry
from app.voice.turns import FinishedTurn, TurnOutput, TurnTracker
from app.voice.web_voice import VoiceWebSearch

logger = logging.getLogger(__name__)

# Long tool text goes to the chat transcript; the model hears only a short excerpt (the spoken
# reply stays short). Text at or under VOICE_CHAT_MIN_CHARS is passed to the model unchanged.
VOICE_CHAT_MIN_CHARS = 600
VOICE_EXCERPT_CHARS = 1_200
CHAT_TOOL_TEXT_MAX_CHARS = 60_000


@dataclass
class VoiceDeps:
    """Everything a connection needs from the application."""

    persistence: VoicePersistenceService
    session_manager: VoiceSessionManager
    redis: Any
    model_gateway: Any
    queue: Any
    api_key: str
    models: list[str]
    connector: Connector | None = None
    key_pool: Any = None
    # Memory in voice (Task V): the chat memory service for this caller and the preferences store
    # that holds the per-user ``memory_in_chat`` toggle. Both None means memory is off.
    memory: VoiceMemory | None = None
    preferences: Any = None
    # Read-only GitHub repository tools (the caller's own connection, or public repos).
    repo_tools: Any = None
    # generate_image (services/image_gen.py); None when images are off.
    images: Any = None
    # The chat web search service (app/search); None disables web_search in voice.
    web_search: Any = None
    # The user's mailbox (app/mail): the READ tools only. Sending stays an approval card.
    mail: Any = None
    # Voice watches the user's tasks and speaks the milestones (MAYA_VOICE_TASK_ANNOUNCE).
    task_announce: bool = True
    # The shared task submission service: lets voice PROPOSE a Notion write (approval-gated, as
    # in chat). None without a database; then voice has the read tools only.
    task_submitter: Any = None


MASTER_SPACING_SECONDS = 10.0
# Most different keys one live connect tries on a quota failure, per model.
LIVE_MAX_QUOTA_KEYS = 8


class VoiceConnection:
    def __init__(
        self,
        websocket: WebSocket,
        principal: Principal,
        deps: VoiceDeps,
        *,
        protocol_version: int = 1,
        requested_session_id: UUID | None = None,
        idle_timeout: float = protocol.IDLE_TIMEOUT_SECONDS,
        max_duration: float = protocol.MAX_DURATION_SECONDS,
        voice_scope: str = protocol.SCOPE_CONVERSATION,
    ) -> None:
        self.ws = websocket
        # "master" = app-wide progress tracker (top-bar voice); anything else = conversation-bound.
        self.voice_scope = protocol.normalize_scope(voice_scope)
        self.principal = principal
        self.deps = deps
        self.v2 = protocol_version >= 2
        self.requested_session_id = requested_session_id
        self.max_duration = max_duration
        # The master tracker is expected to sit quiet (muted mic, nothing to announce): it ends
        # only at the max duration (the client reconnects), never for being idle.
        self.idle_timeout = (
            max_duration if self.voice_scope == protocol.SCOPE_MASTER else idle_timeout
        )

        self.session_id: UUID | None = None
        self.persisted = False
        self.tracker = TurnTracker()
        self._user_said: list[str] = []  # finished user turns, newest last (required-details gate)
        self._user_now = ""  # the user turn that is still open
        self.session: LiveVoiceSession | None = None
        self.bridge: GeminiLiveBridge | None = None
        self.events: VoiceEventBridge | None = None
        self.watcher: TaskAnnouncer | None = None
        self._announced: AnnouncedStore | None = None
        self._user_speaking = False
        self._last_user_speech = 0.0
        self._tasks: list[asyncio.Task[Any]] = []
        self._planning_tasks: set[asyncio.Task[None]] = set()
        self._persist_queue: asyncio.Queue[FinishedTurn | None] = asyncio.Queue()
        self._persist_task: asyncio.Task[None] | None = None
        self._last_activity = time.monotonic()
        self._fatal: dict[str, Any] | None = None
        self._close_code = protocol.CLOSE_NORMAL
        self._scope = uuid4().hex
        self._memory_on = False
        self._repo_on = False
        self._images_on = False
        self._web_on = False
        self._notion_on = False
        self._notion_write_on = False
        self._notion_names: list[str] = []
        self._mail_on = False
        self._schedules_on = False
        self._mcp_on = False
        self._show_code_on = False
        self._chat_on = False
        self._saved_memories: list[SavedMemory] = []  # saved by voice, not yet on a stored reply

    # ---- low-level send --------------------------------------------------------------

    async def _send_direct(self, frame: dict[str, Any]) -> bool:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return False
        try:
            await self.ws.send_json(frame)
            return True
        except Exception:
            return False

    async def _close(self, code: int, reason: str = "") -> None:
        if self.ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await self.ws.close(code=code, reason=reason[:100])

    async def _emit(self, frame: dict[str, Any]) -> None:
        if self.session is not None:
            await self.session.emit_control(frame)

    async def _emit_output(self, out: TurnOutput, legacy_role: str | None = None) -> None:
        for frame in out.frames:
            self._note_user_words(frame)
            await self._emit(frame)
            if not self.v2 and frame.get("type") == "TRANSCRIPT" and not frame["final"]:
                legacy = "TRANSCRIPT_USER" if frame["role"] == "user" else "TRANSCRIPT_MODEL"
                await self._emit({"type": legacy, "text": frame["delta"]})
        for turn in out.finished:
            self._persist_queue.put_nowait(turn)
            if turn.role == "assistant" and self.watcher is not None:
                await self.watcher.on_model_turn(turn.text)

    # ---- setup -----------------------------------------------------------------------

    def _make_bridge(
        self, registry: VoiceToolRegistry, model: str, handle: str | None
    ) -> GeminiLiveBridge:
        context = VoiceExecutionContext(
            workspace_id=self.principal.workspace_id,
            user_id=self.principal.user_id,
            session_id=str(self.session_id) if self.session_id else None,
            scope=self._scope,
        )
        return GeminiLiveBridge(
            api_key=self.deps.api_key,
            system_instruction=build_voice_instruction(
                memory_on=self._memory_on,
                repo_on=self._repo_on,
                images_on=self._images_on,
                web_on=self._web_on,
                master=self.voice_scope == protocol.SCOPE_MASTER,
            ),
            instruction_provider=self._build_instruction,
            tool_registry=registry,
            context=context,
            model=model,
            connector=self.deps.connector,
            resumption_handle=handle,
        )

    async def _build_instruction(self) -> str:
        """The Live instruction with a fresh memory block. Called on every (re)connect, so a
        resumed session also sees memories saved since the last one."""
        block = ""
        if self._memory_on and self.deps.memory is not None:
            block = await connect_memory_block(self.deps.memory)
        return build_voice_instruction(
            memory_on=self._memory_on,
            memory_block=block,
            repo_on=self._repo_on,
            notion_on=self._notion_on,
            notion_write_on=self._notion_write_on,
            notion_names=self._notion_names,
            mail_on=self._mail_on,
            mcp_on=self._mcp_on,
            schedules_on=self._schedules_on,
            show_code_on=self._show_code_on,
            chat_on=self._chat_on,
            images_on=self._images_on,
            web_on=self._web_on,
            master=self.voice_scope == protocol.SCOPE_MASTER,
        )

    async def _on_memory_saved(self, saved: SavedMemory) -> None:
        self._saved_memories.append(saved)
        await self._emit(protocol.memory_saved(str(saved.memory_id), saved.text, saved.updated))

    async def _enable_memory(self, registry: VoiceToolRegistry) -> None:
        """Declare the memory tools only when memory is wired and on for this user."""
        memory = self.deps.memory
        self._memory_on = await memory_enabled(
            memory, self.deps.preferences, self.principal.workspace_id, self.principal.user_id
        )
        if self._memory_on and memory is not None:
            tools = VoiceMemoryTools(memory, on_saved=self._on_memory_saved)
            registry.set_memory_handlers(tools.recall, tools.save)

    def _enable_repo_tools(self, registry: VoiceToolRegistry) -> None:
        """Declare the read-only GitHub tools when the app wired them (always safe: they
        read public repositories without a token and private ones with the user's own)."""
        repo = self.deps.repo_tools
        if repo is None:
            return
        toolkit = repo.new_toolkit(max_calls=80)

        async def github_route(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await self._for_voice(name, await toolkit.call(name, args))

        registry.set_repo_handler(github_route)
        self._repo_on = True

    def _enable_web_tools(self, registry: VoiceToolRegistry) -> None:
        """Declare web_search when the chat search service is wired (read-only)."""
        searcher = self.deps.web_search
        if searcher is None or not hasattr(searcher, "web_search"):
            return
        registry.set_web_handler(
            VoiceWebSearch(
                searcher,
                user_key=str(self.principal.user_id),
                workspace_id=self.principal.workspace_id,
            )
        )
        self._web_on = True

    def _note_user_words(self, frame: dict[str, Any]) -> None:
        """Keep what the user said (open turn and the last finished ones) for the gate."""
        if frame.get("type") != "TRANSCRIPT" or frame.get("role") != "user":
            return
        if frame.get("final"):
            text = str(frame.get("text") or self._user_now).strip()
            if text:
                self._user_said = [*self._user_said, text][-8:]
            self._user_now = ""
        else:
            self._user_now += str(frame.get("delta") or "")

    async def _required_questions(self, instruction: str) -> list[dict[str, Any]] | None:
        """The ask_user questions for what the user's own spoken request lacks, else None.
        With nothing heard yet it fails open (the task service checks again)."""
        spoken = [*self._user_said, self._user_now.strip()]
        spoken = [text for text in spoken if text]
        if not spoken:
            return None
        data = required_details.GateInput(request=spoken[-1], history_user=spoken[:-1])
        missing = required_details.check(data)
        if not missing and required_details.detect_kind(spoken[-1]):
            if not required_details.topic_supported(instruction, spoken):
                missing = required_details.Missing((required_details.SLOT_TOPIC,), "document")
        if not missing:
            return None
        wanted = tuple(
            slot
            for slot in missing.slots
            if slot in (required_details.SLOT_TOPIC, required_details.SLOT_DESTINATION)
        )
        if not wanted:
            return None
        return required_details.build_questions(
            required_details.Missing(wanted, missing.kind, missing.hinglish),
            suggested_topic=required_details.suggestion_from_history(spoken[:-1]),
            notion_connected=True,
        )

    def _enable_ask_tool(self, registry: VoiceToolRegistry) -> None:
        """Declare ask_user when conversations are stored (the card is a stored message, so the
        answer can be matched to it)."""
        registry.set_required_check(self._required_questions)
        if self.deps.persistence.engine is not None:
            registry.set_ask_handler(self._ask_user)

    async def _ask_user(self, args: dict[str, Any]) -> dict[str, Any]:
        """Show a question card in the chat: stored on an assistant message (so a reload shows it)
        and sent to the client. Nothing runs and nothing is approved; the user answers on the card
        and the answer goes through the chat message path."""
        session_id = self.session_id
        questions = ask_user.clamp_questions(args.get("questions"))
        if session_id is None or not questions:
            return dict(ask_user.NOT_AVAILABLE_RESULT)
        card = InputRequestedEvent(
            ask_id=f"ask_{uuid4().hex[:16]}", questions=questions, source="voice"
        )
        try:
            saved = await self.deps.persistence.save_turn(
                self.principal.workspace_id,
                self.principal.user_id,
                session_id,
                role="assistant",
                content=questions[0].question,
                modality="audio",
                metadata={"input_request": ask_user.stored_request(card, "voice-ask", None)},
            )
        except Exception as error:
            logger.warning("voice_ask_persist_failed error=%s", type(error).__name__)
            return dict(ask_user.NOT_AVAILABLE_RESULT)
        await self._emit(protocol.input_requested(card.model_dump(mode="json"), str(saved or "")))
        return {
            "status": "shown",
            "message": (
                "The question card is on screen. Say the question aloud in one short sentence, "
                "then wait for the user's answer. Do not answer for them and do not ask again."
            ),
        }

    def _enable_image_tools(self, registry: VoiceToolRegistry) -> None:
        """Declare generate_image when the image service is wired. The live model only asks for the
        picture; the image model makes it."""
        if self.deps.images is None:
            return
        registry.set_image_handler(self._generate_image)
        self._images_on = True

    async def _generate_image(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run generate_image for this voice session. The image is stored in the session's chat
        folder, saved on a message (so a reload shows it) and sent to the client as a file card."""
        images = self.deps.images
        session_id = self.session_id
        if images is None or session_id is None:
            return {
                "status": "error",
                "message": (
                    "Image creation is not available right now. Say so in one short sentence."
                ),
            }
        raw_prompt = args.get("prompt")
        raw_style = args.get("style_hint")
        try:
            outcome = await images.generate(
                workspace_id=self.principal.workspace_id,
                user_id=self.principal.user_id,
                session_id=session_id,
                prompt=raw_prompt if isinstance(raw_prompt, str) else "",
                style_hint=raw_style if isinstance(raw_style, str) else None,
            )
        except ImageError as error:
            message = f"{error} Nothing was created. Say so in one short sentence."
            return {"status": "error", "message": message}
        except Exception as error:  # an image failure must never end the voice session
            logger.warning("voice_image_failed error=%s", type(error).__name__)
            return {
                "status": "error",
                "message": (
                    "The image could not be made. Nothing was created. "
                    "Say so in one short sentence."
                ),
            }
        card = outcome.created.model_dump(mode="json")
        chat_files: list[dict[str, str]] = []
        if outcome.storage_key is not None:
            chat_files.append(
                {"file_id": str(outcome.created.file_id), "storage_key": outcome.storage_key}
            )
        message_id: str | None = None
        try:
            saved = await self.deps.persistence.save_turn(
                self.principal.workspace_id,
                self.principal.user_id,
                session_id,
                role="assistant",
                content=f"Image created: {outcome.created.name}",
                modality="audio",
                metadata={"files_created": [card], "chat_files": chat_files},
            )
            message_id = str(saved) if saved else None
        except Exception as error:
            logger.warning("voice_image_persist_failed error=%s", type(error).__name__)
        await self._emit(protocol.file_created(card, message_id))
        return dict(outcome.result)

    def _enable_review_tool(self, registry: VoiceToolRegistry) -> None:
        """Declare report_step_review when task updates are spoken. It must be declared before
        the provider session opens (the declarations are part of the connect config); the
        announcer that answers it is created right after the connect."""
        if self.deps.task_announce and self.deps.persistence.engine is not None:
            registry.set_review_handler(self._on_step_review)

    async def _on_step_review(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.watcher is None:
            return {"ok": False, "error": "unavailable", "message": "No review is possible now."}
        return await self.watcher.record_review(args)

    async def _enable_connector_tools(self, registry: VoiceToolRegistry) -> None:
        """Declare the read-only Notion tools when the caller has a Notion connection, and the
        show_code tool (GitHub, connected folder or upload), with the caller's own connections."""
        repo = self.deps.repo_tools
        mailbox = MailToolkit(self.deps.mail) if self.deps.mail is not None else None
        # External MCP tools (ADR-0028): the same registry, tiers and approval path as chat.
        mcp_service = mcp_runtime.current()
        mcp_turn = await mcp_voice.prepare_voice(
            mcp_service,
            McpIdentity(self.principal.workspace_id, self.principal.user_id),
            max_tools=mcp_service.settings.max_tools_per_turn if mcp_service else 0,
        )
        schedules = schedule_runtime.current() if self.deps.persistence.engine is not None else None
        if repo is None and mailbox is None and mcp_turn is None and schedules is None:
            return
        notion_on = repo is not None and await repo.notion_available("")
        notion = repo.new_notion_toolkit(max_calls=80) if repo is not None else None
        show = repo.new_show_code_toolkit() if repo is not None else None

        write_on = notion_on and self.deps.task_submitter is not None

        async def connector_route(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if mcp_turn is not None and name in mcp_turn.names:
                return await mcp_voice.call_by_voice(
                    mcp_turn,
                    self.deps.task_submitter,
                    name=name,
                    args=args,
                    workspace_id=self.principal.workspace_id,
                    user_id=self.principal.user_id,
                    session_id=str(self.session_id) if self.session_id else None,
                    scope=self._scope,
                )
            if schedules is not None and name in VOICE_SCHEDULE_TOOL_NAMES:
                return await self._schedule_by_voice(schedules, name, args)
            if mailbox is not None and name in MAIL_READ_NAMES:
                outcome = await mailbox.call(name, args)
            elif name == SHOW_CODE_NAME and show is not None:
                outcome = await show.call(args)
            elif name == SAVE_EXISTING and notion is not None and write_on:
                # Existing notes saved as they are: propose only, never a model rewrite.
                history = (
                    await self.deps.persistence.load_seed(
                        self.principal.workspace_id, self.principal.user_id, self.session_id, 20
                    )
                    if self.session_id is not None
                    else []
                )
                return await save_existing_by_voice(
                    notion,
                    self.deps.task_submitter,
                    history,
                    destination=str(args.get("destination") or ""),
                    as_is=args.get("as_is") is True,
                    workspace_id=self.principal.workspace_id,
                    user_id=self.principal.user_id,
                    session_id=str(self.session_id) if self.session_id else None,
                    scope=self._scope,
                )
            elif name in VOICE_NOTION_WRITE_NAMES and notion is not None and write_on:
                # Propose only: the approval card appears in the chat; nothing is written here.
                return await propose_by_voice(
                    notion,
                    self.deps.task_submitter,
                    name=name,
                    args=args,
                    workspace_id=self.principal.workspace_id,
                    user_id=self.principal.user_id,
                    session_id=str(self.session_id) if self.session_id else None,
                    scope=self._scope,
                    user_text=" ".join([*self._user_said[-3:], self._user_now]),
                )
            elif notion is not None:
                outcome = await notion.call(name, args)
            else:
                outcome = {"ok": False, "error": "That tool is not available."}
            return await self._for_voice(name, outcome)

        names: list[str] = []
        declarations: list[dict[str, Any]] = []
        if repo is not None:
            names.append(SHOW_CODE_NAME)
            declarations.append(show_code_voice_declaration())
        if notion_on:
            names.extend(NOTION_TOOL_NAMES)
            declarations.extend(notion_voice_declarations())
            if write_on:
                names.extend(VOICE_NOTION_WRITE_NAMES)
                declarations.extend(notion_write_voice_declarations())
                names.append(SAVE_EXISTING)
                declarations.extend(save_existing_declarations())
        if mailbox is not None:
            names.extend(MAIL_READ_NAMES)
            declarations.extend(mail_voice_declarations())
        if mcp_turn is not None:
            names.extend(mcp_turn.names)
            declarations.extend(mcp_voice.declarations(mcp_turn))
        if schedules is not None:
            names.extend(VOICE_SCHEDULE_TOOL_NAMES)
            declarations.extend(schedule_voice_declarations())
        registry.set_connector_tools(connector_route, tuple(names), declarations)
        self._mcp_on = mcp_turn is not None
        self._notion_on = notion_on
        self._notion_write_on = write_on
        # The user's real database names, read once per connection (capped), so the model can map
        # a misheard name to them. Empty when Notion has none or cannot be read.
        self._notion_names = (
            await database_names(notion) if notion_on and notion is not None else []
        )
        self._show_code_on = repo is not None
        self._mail_on = mailbox is not None
        self._schedules_on = schedules is not None

    async def _schedule_by_voice(
        self, services: ScheduleServices, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Schedule tools by voice. ``schedule_report`` only PROPOSES: the preview card is stored
        in the chat and sent to the client; the user must click Create (never a spoken yes)."""
        ws, user = self.principal.workspace_id, self.principal.user_id
        doc = await services.service.get_doc(ws, user)
        kit = ScheduleToolkit(
            services=services,
            workspace_id=ws,
            user_id=user,
            default_timezone=doc.settings.timezone or None,
            user_texts=lambda: [*self._user_said, self._user_now],
            voice=True,
        )
        result = await kit.call(name, args)
        for draft in kit.new_drafts[:1]:
            card = ScheduleDraftEvent(
                draft_id=draft.id,
                name=str(draft.schedule.get("name") or ""),
                preview=draft.preview,
                schedule=draft.schedule,
                expires_at=draft.expires_at.isoformat(),
            )
            saved = None
            if self.session_id is not None:
                try:
                    saved = await self.deps.persistence.save_turn(
                        ws,
                        user,
                        self.session_id,
                        role="assistant",
                        content=draft.preview,
                        modality="audio",
                        metadata={"schedule_drafts": [card.model_dump(mode="json")]},
                    )
                except Exception as error:  # noqa: BLE001
                    logger.warning("voice_schedule_persist_failed error=%s", type(error).__name__)
            await self._emit(
                protocol.schedule_draft(card.model_dump(mode="json"), str(saved or ""))
            )
        return result

    async def _for_voice(self, name: str, outcome: dict[str, Any]) -> dict[str, Any]:
        """What the voice model hears from one read tool. Long text (and every show_code result)
        goes to the chat transcript as an assistant message; the model gets a short excerpt."""
        if not outcome.get("ok"):
            return outcome
        text = str(outcome.get("result") or "")
        is_code = name == SHOW_CODE_NAME
        if not is_code and len(text) <= VOICE_CHAT_MIN_CHARS:
            # Nothing was posted to the chat: say so, so the model never claims it was.
            return {**outcome, "posted_to_chat": False}
        posted = await self._post_to_chat(name, text)
        if is_code:
            if posted:
                return {
                    "ok": True,
                    "posted_to_chat": True,
                    "message": "The code is in the chat. Say only: I put the code in the chat.",
                }
            return {
                "ok": False,
                "error": "The code could not be put in the chat. Say you could not show it.",
            }
        note = (
            "The full result is in the chat. Summarise it in one or two short spoken sentences; "
            "do not read it out."
            if posted
            else "The full result could not be saved to the chat. Summarise what you have in one "
            "short spoken sentence."
        )
        return {
            "ok": True,
            "result": text[:VOICE_EXCERPT_CHARS],
            "truncated_for_voice": len(text) > VOICE_EXCERPT_CHARS,
            "message": note,
        }

    def _enable_chat_tool(self, registry: VoiceToolRegistry) -> None:
        """Declare show_in_chat whenever messages can be stored (no connector needed)."""
        if self.deps.persistence.engine is not None:
            registry.set_chat_handler(self._show_in_chat)
            self._chat_on = True

    async def _show_in_chat(self, args: dict[str, Any]) -> dict[str, Any]:
        """show_in_chat: save the model's own Markdown as an assistant message and send it live.
        Never reports success unless it was saved."""
        content = str(args.get("content") or "").strip()
        if not content:
            return {
                "ok": False,
                "error": "There was nothing to show. Write the complete content, then call again.",
            }
        title = str(args.get("title") or "").strip()
        if title and not content.startswith("#"):
            content = f"### {title}\n\n{content}"
        if await self._post_to_chat(SHOW_IN_CHAT, content):
            return {
                "ok": True,
                "posted_to_chat": True,
                "message": (
                    "It is in the chat. Say one short sentence, for example: I put it in the "
                    "chat. Do not read it aloud."
                ),
            }
        return {
            "ok": False,
            "error": "It could not be put in the chat. Say you could not show it.",
        }

    async def _post_to_chat(self, name: str, text: str) -> bool:
        """Save one tool result as an assistant message of this conversation and send it to the
        client live. False when there is no conversation or no database (nothing is saved or
        sent, nothing is raised to the model)."""
        session_id = self.session_id
        if session_id is None:
            return False
        body = text
        if len(body) > CHAT_TOOL_TEXT_MAX_CHARS:
            body = body[:CHAT_TOOL_TEXT_MAX_CHARS] + "\n[truncated: the rest was not saved]"
        try:
            saved = await self.deps.persistence.save_turn(
                self.principal.workspace_id,
                self.principal.user_id,
                session_id,
                role="assistant",
                content=body,
                modality="text",
                metadata={"source": "voice_tool", "tool": name},
            )
        except VoicePersistenceError:
            logger.warning("voice_tool_chat_post_failed tool=%s", name)
            return False
        if saved is None:
            return False
        await self._emit(protocol.chat_message(str(saved), body, name))
        return True

    async def _fail_setup(self, frame: dict[str, Any], code: int, reason: str) -> None:
        await self._send_direct(frame)
        await self._close(code, reason)

    async def _setup(self, registry: VoiceToolRegistry) -> bool:
        ws_id, user_id = self.principal.workspace_id, self.principal.user_id
        persistence = self.deps.persistence
        history: list[tuple[str, str]] = []
        handle: str | None = None
        title: str | None = None
        await self._enable_memory(registry)
        self._enable_repo_tools(registry)
        await self._enable_connector_tools(registry)
        self._enable_image_tools(registry)
        self._enable_web_tools(registry)
        self._enable_ask_tool(registry)
        self._enable_chat_tool(registry)
        self._enable_review_tool(registry)

        if self.requested_session_id is not None:
            try:
                info = await persistence.get_session(ws_id, user_id, self.requested_session_id)
            except SessionNotOwned:
                await self._fail_setup(
                    protocol.error("session_not_found", "Conversation not found.", fatal=True),
                    protocol.CLOSE_SESSION_NOT_FOUND,
                    "session_not_found",
                )
                return False
            except VoicePersistenceError:
                await self._fail_setup(
                    protocol.error("persistence_unavailable", "Storage unavailable.", fatal=True),
                    protocol.CLOSE_UNAVAILABLE,
                    "persistence_unavailable",
                )
                return False
            self.session_id, title = info.id, info.title
            self.persisted = persistence.enabled
            handle = await self.deps.session_manager.get_resumption_token(ws_id, str(info.id))
            try:
                history = await persistence.load_seed(
                    ws_id, user_id, info.id, protocol.SEED_HISTORY_MESSAGES
                )
            except VoicePersistenceError:
                history = []  # Live still works without history; reported by the log only
                logger.warning("voice_seed_load_failed session_id=%s", info.id)

        bridge: GeminiLiveBridge | None = None
        last_reason = "connect_failed"
        resumed = False
        models_tried: set[str] = set()
        keys_tried: set[str] = set()
        for model in self.deps.models:
            for use_handle in [handle, None] if handle else [None]:
                candidate = self._make_bridge(registry, model, use_handle)
                try:
                    await candidate.connect(history=history)
                except LiveUnavailable as err:
                    last_reason = err.reason
                    models_tried.add(model)
                    _pool = getattr(self.deps, "key_pool", None)
                    keys_tried.add((_pool.label_for(self.deps.api_key) if _pool else None) or "key")
                    if (
                        err.reason == "quota"
                        and getattr(self.deps, "key_pool", None)
                        and not self.deps.key_pool.is_empty
                    ):
                        # Quota is per key: rotate through other keys (a few at most) before
                        # moving to the next live model.
                        pool = self.deps.key_pool
                        pool.report_failure(self.deps.api_key, "quota")
                        used_labels = {pool.label_for(self.deps.api_key) or ""}
                        keys_tried.update(used_labels)
                        rotated = False
                        for _ in range(min(len(pool), LIVE_MAX_QUOTA_KEYS) - 1):
                            try:
                                next_key, next_label = pool.get_next(exclude=used_labels)
                            except ValueError:
                                break
                            used_labels.add(next_label)
                            keys_tried.add(next_label)
                            self.deps.api_key = next_key.get_secret_value()
                            logger.info(
                                "voice_live_key_failover reason=quota model=%s next=%s",
                                model,
                                next_label,
                            )
                            candidate = self._make_bridge(registry, model, use_handle)
                            try:
                                await candidate.connect(history=history)
                            except LiveUnavailable as retry_err:
                                last_reason = retry_err.reason
                                if retry_err.reason in ("no_key", "disabled"):
                                    break
                                if retry_err.reason == "quota":
                                    pool.report_failure(self.deps.api_key, "quota")
                                    continue
                                break
                            pool.report_success(next_key)
                            bridge, resumed = candidate, use_handle is not None
                            rotated = True
                            break
                        if rotated:
                            break
                        if last_reason in ("no_key", "disabled"):
                            break
                        continue
                    if err.reason in ("no_key", "disabled"):
                        break
                    continue
                else:
                    if getattr(self.deps, "key_pool", None) and self.deps.api_key:
                        self.deps.key_pool.report_success(self.deps.api_key)
                bridge, resumed = candidate, use_handle is not None
                break
            if bridge is not None or last_reason in ("no_key", "disabled"):
                break
        if bridge is None:
            await self._fail_setup(
                protocol.live_unavailable(
                    last_reason,
                    str(self.session_id) if self.session_id else None,
                    models_tried=len(models_tried),
                    keys_tried=len(keys_tried),
                ),
                protocol.CLOSE_UNAVAILABLE,
                "live_unavailable",
            )
            return False
        self.bridge = bridge

        if self.session_id is None:
            try:
                created = await persistence.create_session(ws_id, user_id)
            except VoicePersistenceError:
                await bridge.close()
                await self._fail_setup(
                    protocol.error("persistence_unavailable", "Storage unavailable.", fatal=True),
                    protocol.CLOSE_UNAVAILABLE,
                    "persistence_unavailable",
                )
                return False
            self.session_id, title = created.id, created.title
            self.persisted = persistence.enabled

        assert self.session_id is not None
        sid = str(self.session_id)
        bridge.set_context(
            VoiceExecutionContext(
                workspace_id=ws_id, user_id=user_id, session_id=sid, scope=self._scope
            )
        )
        self.session = LiveVoiceSession(
            workspace_id=ws_id,
            user_id=user_id,
            session_id=sid,
            tool_registry=registry,
            model_gateway=self.deps.model_gateway,
        )
        self.events = VoiceEventBridge(
            workspace_id=ws_id, redis_client=self.deps.redis, session_id=sid
        )
        await self._start_watcher(ws_id, user_id)
        await self.events.start_listening()
        self._planning_tasks = wire_voice_tools(
            tool_registry=registry,
            event_bridge=self.events,
            db_engine=self.deps.persistence.engine,
            model_gateway=self.deps.model_gateway,
            queue=self.deps.queue,
        )
        await self._send_direct(
            protocol.ready(
                sid, resumable=True, persisted=self.persisted, resumed=resumed, title=title
            )
        )
        await self._send_direct(protocol.turn_state(None, "listening"))
        return True

    def _voice_busy(self) -> bool:
        """True while the model speaks or thinks, or the user is (or just was) speaking."""
        session = self.session
        if session is None:
            return True
        return (
            self._user_speaking
            or time.monotonic() - self._last_user_speech < 2.0
            or session.is_speaking
            or session.model_turn_active
            or session.pending_audio_chunks_count > 0
            or self.tracker.state != "listening"
        )

    async def _start_watcher(self, ws_id: UUID, user_id: UUID) -> None:
        """Watch every task of this user (chat, Run as task or voice) and speak the milestones."""
        engine = self.deps.persistence.engine
        if not self.deps.task_announce or engine is None or self.events is None:
            return
        assert self.bridge is not None
        bridge = self.bridge
        master = self.voice_scope == protocol.SCOPE_MASTER

        async def speak(text: str) -> None:
            # The configured live model speaks it; no other model is called for task updates.
            await bridge.inject_task_digest(text)

        store = AnnouncedStore(redis=self.deps.redis)
        self._announced = store
        watcher = TaskAnnouncer(
            workspace_id=ws_id,
            user_id=user_id,
            resolver=make_db_resolver(engine),
            store=store,
            speak=speak,
            is_busy=self._voice_busy,
            notify=self._emit,
            review_store=make_review_store(engine),
            can_report_review=True,
            memory_on=self._memory_on,
            name_conversation=master,
            # Master voice: no more than one non-urgent spoken update per ~10 s. Urgent ones
            # (failure, input, approval, final result) still skip the spacing.
            spacing_seconds=MASTER_SPACING_SECONDS if master else SPACING_SECONDS,
        )
        self.watcher = watcher
        self.events.watcher = watcher
        try:
            missed = await find_catch_up(engine, store, ws_id, user_id)
        except Exception as err:
            logger.warning("voice_catch_up_failed type=%s", type(err).__name__)
            missed = []
        if missed:
            watcher.add_catch_up([(m, f) for m, f, _k in missed], [k for _m, _f, k in missed])
        schedules = schedule_runtime.current()
        if master and schedules is not None:
            # Finished scheduled reports: spoken once on open (the web app starts master voice
            # muted when GET /schedules/updates has items; the backend never opens the mic).
            for text, on_spoken in await voice_updates(
                schedules.service.updates, schedules.service.ack, ws_id, user_id
            ):
                watcher.add_schedule_update(text, on_spoken)
        watcher.start()

    # ---- loops -----------------------------------------------------------------------

    async def _sender(self) -> None:
        assert self.session is not None
        while True:
            msg = await self.session.get_next_outgoing_message()
            if msg.msg_type == OutgoingMessageType.AUDIO:
                await self.ws.send_bytes(msg.audio_bytes)
            else:
                await self.ws.send_json(msg.payload)

    async def _persist_worker(self) -> None:
        assert self.session_id is not None
        while True:
            turn = await self._persist_queue.get()
            try:
                if turn is None:
                    return
                spoken = turn.source == "spoken" or turn.role == "assistant"
                metadata: dict[str, Any] = {
                    "turn_id": turn.turn_id,
                    "source": turn.source,
                    "interrupted": turn.interrupted,
                    "incomplete": turn.incomplete,
                }
                if turn.role == "assistant" and self._saved_memories:
                    # Same shape as chat, so the UI chip appears after a reload too.
                    metadata["memories_saved"] = [m.as_metadata() for m in self._saved_memories]
                    self._saved_memories = []
                try:
                    message_id = await self.deps.persistence.save_turn(
                        self.principal.workspace_id,
                        self.principal.user_id,
                        self.session_id,
                        role=turn.role,
                        content=turn.text,
                        modality="audio" if spoken else "text",
                        metadata=metadata,
                    )
                except VoicePersistenceError:
                    logger.error("voice_turn_persist_failed turn_id=%s", turn.turn_id)
                    await self._emit(
                        protocol.error("persist_failed", "A turn could not be saved.", fatal=False)
                    )
                    continue
                if message_id is not None:
                    await self._emit(
                        protocol.message_persisted(turn.turn_id, str(message_id), turn.role)
                    )
            finally:
                self._persist_queue.task_done()

    async def _rx(self) -> None:
        assert self.bridge is not None
        async for event in self.bridge.receive_events():
            try:
                await self._on_event(event)
            except Exception as err:
                logger.error("voice_event_handler_failed type=%s", type(err).__name__)
        if self._fatal is None:
            self._fatal = protocol.error(
                "live_session_closed", "The voice session ended.", fatal=True
            )

    async def _on_event(self, event: dict[str, Any]) -> None:
        assert self.session is not None
        session = self.session
        kind = event.get("type")
        if kind == "USER_TRANSCRIPT":
            self._last_user_speech = time.monotonic()
        if kind == "MODEL_AUDIO":
            if session.dropping_stale_audio:
                return
            out = self.tracker.model_output()
            await self._emit_output(out)
            await session.enqueue_model_audio(event["data"])
        elif kind == "USER_TRANSCRIPT":
            await self._emit_output(self.tracker.user_delta(event.get("text", "")))
        elif kind == "MODEL_TRANSCRIPT":
            if session.dropping_stale_audio:
                return
            await self._emit_output(self.tracker.model_output(event.get("text", "")))
        elif kind == "INTERRUPTED":
            await session.handle_interrupt(source="gemini_interrupted")
            await session.end_model_turn()
            await self._emit_output(self.tracker.interrupted())
        elif kind == "TURN_COMPLETE":
            await session.end_model_turn()
            await self._emit_output(self.tracker.turn_complete())
        elif kind == "GO_AWAY":
            await self._emit(protocol.go_away(event.get("retry_ms"), bool(event.get("resumable"))))
        elif kind == "SESSION_RENEWED":
            await self._emit({"type": "SESSION_RENEWED"})
        elif kind == "RESUMPTION":
            await self.deps.session_manager.store_resumption_token(
                self.principal.workspace_id,
                str(self.session_id),
                event["handle"],
                ttl_seconds=protocol.RESUMPTION_TTL_SECONDS,
            )
        elif kind == "TOOL_EXECUTED":
            await self._on_tool_executed(event)
        elif kind == "TOOL_ERROR":
            await self._emit(
                protocol.error(
                    "tool_refused"
                    if event.get("code") in ("approval_prohibited", "unsupported_tool")
                    else "tool_failed",
                    "That action is not available by voice."
                    if event.get("code") == "approval_prohibited"
                    else "A tool call failed.",
                    fatal=False,
                )
            )
        elif kind == "ERROR":
            frame = protocol.error(
                str(event.get("code", "error")),
                str(event.get("message", "Voice error.")),
                fatal=bool(event.get("fatal")),
            )
            if frame["fatal"]:
                self._fatal = frame
            else:
                await self._emit(frame)

    async def _on_tool_executed(self, event: dict[str, Any]) -> None:
        result = event.get("result") or {}
        args = event.get("args") or {}
        task_id = result.get("task_id")
        if result.get("status") == "error" or not task_id:
            if result.get("error"):
                await self._emit(
                    protocol.error(
                        str(result["error"]), str(result.get("message", "")), fatal=False
                    )
                )
            return
        await self._emit(
            {
                "type": "TASK_STATUS",
                "task_id": task_id,
                "status": result.get("status", "pending"),
                "intent": args.get("instruction")
                or args.get("intent")
                or event.get("name", "task"),
                "tool_name": event.get("name", "submit_task"),
                "args": args,
                "result": result,
            }
        )

    async def _announcements(self) -> None:
        assert self.events is not None and self.bridge is not None
        while True:
            announcement = await self.events.get_next_announcement()
            try:
                await self._emit(
                    protocol.task_event(
                        announcement.task_id, announcement.event_type, announcement.payload
                    )
                )
                # Never spoken from here: the TaskAnnouncer owns every spoken task update.
            except Exception as err:
                logger.warning("voice_announcement_failed type=%s", type(err).__name__)

    async def _barge_in(self, source: str) -> None:
        assert self.session is not None
        await self.session.handle_interrupt(source=source)
        await self._emit_output(self.tracker.interrupted())

    async def _handle_text_frame(self, raw: str) -> None:
        assert self.session is not None and self.bridge is not None
        if len(raw) > protocol.MAX_TEXT_FRAME_CHARS:
            await self._emit(protocol.error("frame_too_large", "Frame too large.", fatal=False))
            return
        try:
            payload = json.loads(raw)
            msg_type = payload.get("type") if isinstance(payload, dict) else None
        except ValueError:
            await self._emit(protocol.error("bad_frame", "Invalid JSON frame.", fatal=False))
            return

        if msg_type == "USER_TEXT":
            text = str(payload.get("text", "")).strip()[: protocol.MAX_USER_TEXT_CHARS]
            if not text:
                return
            out = self.tracker.user_text(text)
            await self._emit_output(out)
            if not self.v2:
                await self._emit({"type": "TRANSCRIPT_USER", "text": text})
            await self.bridge.send_text_input(text)
        elif msg_type == "CLIENT_INTERRUPT":
            await self._barge_in("client_interrupt")
        elif msg_type == "VAD_SPEECH_START":
            self._user_speaking = True
            self._last_user_speech = time.monotonic()
            s = self.session
            if s.is_speaking or s.pending_audio_chunks_count > 0 or s.model_turn_active:
                await self._barge_in("client_vad_speech_start")
        elif msg_type == "VAD_SPEECH_STOP":
            self._user_speaking = False
            self._last_user_speech = time.monotonic()
            self.session.is_interrupted = False
            await self._emit_output(self.tracker.user_input_ended())
            await self.bridge.send_turn_end()
        elif msg_type == "HELLO":
            return  # only meaningful as the first frame; already handled by the handshake
        else:
            await self.session.handle_client_control_message(
                payload if isinstance(payload, dict) else {}
            )

    async def _reader(self) -> None:
        assert self.session is not None and self.bridge is not None
        while True:
            message = await self.ws.receive()
            if message.get("type") == "websocket.disconnect":
                return
            self._last_activity = time.monotonic()
            raw_bytes = message.get("bytes")
            if raw_bytes:
                if len(raw_bytes) > protocol.MAX_BINARY_FRAME_BYTES:
                    self._fatal = protocol.error(
                        "frame_too_large", "Audio frame too large.", fatal=True
                    )
                    self._close_code = protocol.CLOSE_MESSAGE_TOO_BIG
                    return
                # Mic audio is always forwarded, including while MAYA speaks, so the provider's
                # own activity detection can interrupt the model.
                await self.session.process_client_audio(raw_bytes)
                await self.bridge.send_audio_chunk(raw_bytes)
            raw_text = message.get("text")
            if raw_text:
                await self._handle_text_frame(raw_text)

    # ---- supervision -----------------------------------------------------------------

    async def run(self) -> None:
        registry = VoiceToolRegistry()
        started = time.monotonic()
        try:
            if not await self._setup(registry):
                return
            assert self.session_id is not None
            logger.info(
                "voice_connection_ready workspace_id=%s user_id=%s session_id=%s",
                self.principal.workspace_id,
                self.principal.user_id,
                self.session_id,
            )
            self._persist_task = asyncio.create_task(self._persist_worker())
            reader = asyncio.create_task(self._reader())
            self._tasks = [
                asyncio.create_task(self._sender()),
                asyncio.create_task(self._rx()),
                asyncio.create_task(self._announcements()),
                self._persist_task,
                reader,
            ]
            await self._supervise(reader, started)
        finally:
            await self._cleanup()

    async def _supervise(self, reader: asyncio.Task[Any], started: float) -> None:
        while True:
            now = time.monotonic()
            idle_at = self._last_activity + self.idle_timeout
            end_at = started + self.max_duration
            timeout = max(0.0, min(idle_at, end_at) - now)
            done, _ = await asyncio.wait(
                self._tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                now = time.monotonic()
                if now >= end_at:
                    self._fatal = protocol.error(
                        "max_duration", "The voice session reached its time limit.", fatal=True
                    )
                elif now >= self._last_activity + self.idle_timeout:
                    self._fatal = protocol.error(
                        "idle_timeout", "The voice session was idle too long.", fatal=True
                    )
                else:
                    continue
                return
            if reader in done and reader.exception() is None and self._fatal is None:
                return  # client left
            if self._fatal is None:
                failed = next((t for t in done if t.exception() is not None), None)
                if failed is not None:
                    logger.error("voice_loop_died type=%s", type(failed.exception()).__name__)
                    self._fatal = protocol.error(
                        "internal_error", "The voice session failed.", fatal=True
                    )
                elif reader not in done:
                    self._fatal = protocol.error(
                        "internal_error", "The voice session stopped.", fatal=True
                    )
            if self._fatal is not None:
                if self._close_code == protocol.CLOSE_NORMAL:
                    self._close_code = (
                        protocol.CLOSE_NORMAL
                        if self._fatal["code"] in ("idle_timeout", "max_duration")
                        else protocol.CLOSE_UNAVAILABLE
                    )
                return

    async def _cleanup(self) -> None:
        for task in self._tasks:
            if task is not self._persist_task:
                task.cancel()
        for planning in list(self._planning_tasks):
            planning.cancel()
        for task in self._tasks:
            if task is not self._persist_task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._persist_task is not None:
            await self._emit_output_safe(self.tracker.flush())
            self._persist_queue.put_nowait(None)
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._persist_task, timeout=5.0)
            self._persist_task.cancel()
        if self.bridge is not None:
            await self.bridge.close()
        if self.events is not None:
            await self.events.close()
        if self.watcher is not None:
            await self.watcher.close()
        if self._announced is not None and self.session_id is not None:
            # Tasks that finish from now on are caught up by the next voice session.
            with contextlib.suppress(Exception):
                await self._announced.set_mark(
                    self.principal.workspace_id, self.principal.user_id, datetime.now(UTC)
                )
        if self.session is not None:
            await self.session.close()
        if self._fatal is not None:
            await self._send_direct(self._fatal)
        await self._close(self._close_code, self._fatal["code"] if self._fatal else "closed")
        logger.info("voice_connection_closed session_id=%s", self.session_id)

    async def _emit_output_safe(self, out: TurnOutput) -> None:
        for turn in out.finished:
            self._persist_queue.put_nowait(turn)


def build_models(resources: Any, settings: Any) -> list[str]:
    """Model ids for the registry alias ``live`` and its fallback chain (D13)."""
    from app.gateway.registry import build_default_registry

    registry = getattr(resources, "registry", None)
    chain: list[str] = []
    try:
        if registry is None or not hasattr(registry, "resolve_fallback_chain"):
            raise TypeError
        for alias in registry.resolve_fallback_chain("live"):
            model_id = registry.get(alias).model_id
            if isinstance(model_id, str) and model_id not in chain:
                chain.append(model_id)
    except Exception:
        chain = []
    if not chain:
        overrides = getattr(settings, "gemini_model_overrides", None)
        default = build_default_registry(overrides if isinstance(overrides, dict) else None)
        chain = [default.get(alias).model_id for alias in default.resolve_fallback_chain("live")]
    return chain


ConnectorFactory = Callable[[], Connector]
