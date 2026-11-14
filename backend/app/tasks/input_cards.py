"""The question card of a paused step (``needs_input``): post it, and put it back when it is lost.

A task that paused for a detail waits on a card in its chat. The card stops being answerable when
a later user message follows it (``ask_user.pending_request`` only treats the newest card as open),
so a paused task could end up with no way to answer. ``repost_missing_cards`` finds those tasks and
posts the same question again, once per process and ask id. The task card on the web can also
answer the question itself (``POST /api/v1/tasks/{id}/input``), so the chat card is a convenience.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subtask, SubtaskStatus, Task
from app.tasks.needs_input import NEEDS_INPUT_KEY

logger = logging.getLogger("maya")

HISTORY_SCAN: Final = 80
_REPOSTED: Final[set[str]] = set()  # ask ids put back by this process (once each)


async def post_card(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    chat_session_id: UUID,
    task_id: UUID,
    ask_id: str,
    question: str,
    stored: dict[str, Any],
    reason: str = "paused",
) -> bool:
    """Add the card message to the task's chat. Logs ``ask_id``, task and the outcome; never
    raises (the pause stands without the chat card)."""
    from app.db.repositories.chat import ChatRepository

    try:
        repo = ChatRepository(await session.connection(), workspace_id, user_id)
        async with session.begin_nested():
            await repo.add_message(
                chat_session_id,
                "orchestrator",
                question,
                metadata={"input_request": stored},
            )
    except Exception as error:  # noqa: BLE001 - the pause still stands
        logger.warning(
            "needs_input_card_post ask_id=%s task=%s ok=false reason=%s error=%s",
            ask_id,
            task_id,
            reason,
            type(error).__name__,
        )
        return False
    logger.info(
        "needs_input_card_post ask_id=%s task=%s ok=true reason=%s", ask_id, task_id, reason
    )
    return True


def _rebuilt_card(pause: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """A card from the stored options when no earlier card of this ask id can be found."""
    from app.schemas.chat import InputRequestedEvent
    from app.services import ask_user

    options = pause.get("options")
    labels = [str(k) for k in options] if isinstance(options, Mapping) else []
    question = "Which Notion page should the book go under?"
    raw = [
        {
            "question": question,
            "header": "Notion page",
            "options": [{"label": label} for label in labels[:6]] if len(labels) >= 2 else [],
            "allow_text": True,
        }
    ]
    clamped = ask_user.clamp_questions(raw)
    if not clamped:
        return None
    card = InputRequestedEvent(ask_id=str(pause["ask_id"]), questions=clamped, source="task")
    return question, ask_user.stored_request(card, "task-input", None)


async def repost_missing_cards(session: AsyncSession, workspace_id: UUID) -> int:
    """Post again the question of every paused task whose card is no longer answerable in its
    chat. Idempotent: a card that is still the open one is left alone; a card put back stays the
    newest one. Returns the cards posted."""
    from app.db.repositories.chat import ChatRepository
    from app.services import ask_user

    posted = 0
    tasks = (
        (
            await session.execute(
                select(Task).where(
                    Task.workspace_id == workspace_id, Task.status == "awaiting_approval"
                )
            )
        )
        .scalars()
        .all()
    )
    for task in tasks:
        subtasks = (
            (
                await session.execute(
                    select(Subtask).where(
                        Subtask.task_id == task.id,
                        Subtask.workspace_id == workspace_id,
                        Subtask.status == SubtaskStatus.AWAITING_APPROVAL,
                    )
                )
            )
            .scalars()
            .all()
        )
        for subtask in subtasks:
            pause = (subtask.input_data or {}).get(NEEDS_INPUT_KEY)
            if not isinstance(pause, dict) or not pause.get("ask_id"):
                continue
            ask_id = str(pause["ask_id"])
            if ask_id in _REPOSTED:
                continue
            try:
                repo = ChatRepository(await session.connection(), workspace_id, task.user_id)
                records = await repo.recent_messages(task.session_id, HISTORY_SCAN)
            except Exception as error:  # noqa: BLE001 - try again on the next pass
                logger.warning(
                    "needs_input_card_scan ask_id=%s task=%s error=%s",
                    ask_id,
                    task.id,
                    type(error).__name__,
                )
                continue
            open_card = ask_user.pending_request(records)
            if open_card is not None and open_card[1].get("ask_id") == ask_id:
                continue
            earlier = next(
                (
                    (r.metadata or {}).get("input_request")
                    for r in reversed(records)
                    if isinstance(((r.metadata or {}).get("input_request") or {}), dict)
                    and ((r.metadata or {}).get("input_request") or {}).get("ask_id") == ask_id
                ),
                None,
            )
            if isinstance(earlier, dict):
                stored = dict(earlier)
                question = "Which Notion page should the book go under?"
            else:
                rebuilt = _rebuilt_card(pause)
                if rebuilt is None:
                    continue
                question, stored = rebuilt
            stored["task_input"] = {
                "task_id": str(task.id),
                "subtask_id": str(subtask.id),
                "kind": str(pause.get("kind") or ""),
            }
            if await post_card(
                session,
                workspace_id=workspace_id,
                user_id=task.user_id,
                chat_session_id=task.session_id,
                task_id=task.id,
                ask_id=ask_id,
                question=question,
                stored=stored,
                reason="repost",
            ):
                _REPOSTED.add(ask_id)
                posted += 1
    return posted
