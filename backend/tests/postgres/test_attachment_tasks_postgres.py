"""An attached file becomes the source of the task it is sent for (ADR-0022), on real PostgreSQL.

The owner's case: the file is attached on one chat turn ("make ppt of this"), the next turn is a
short confirmation ("haan"). The follow-up carries no attachment, so the task is started with the
session's latest upload. The steps are stored with that upload id, and the document step's prompt
holds the file's text. Fakes only for the model; the planner runs without a gateway (the
single-step fallback, the path that failed for the owner)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ChatMessage, ChatSession, Subtask
from app.db.tenant import tenant_session
from app.files import services as file_services
from app.files.attachments import attachment_dependency_outputs
from app.services.task_submission import TaskSubmissionService
from app.tasks.dispatch import DocumentSpec, FleetSupport, SlideSpec
from app.tasks.queue import SubtaskQueue
from tests.support.files import make_services

pytestmark = pytest.mark.postgres

BUDGET = "Budget for the launch: 5000 euros for travel and 1200 euros for print."


class CapturingGateway:
    client = None

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.prompts.append(str(kwargs["contents"][0]))
        return DocumentSpec(
            file_type="pptx",
            filename="launch.pptx",
            title="Launch budget",
            slides=[SlideSpec(layout="bullets", title="Budget", bullets=["Travel 5000 EUR"])],
        )


@pytest.fixture
def files(tmp_path: Path) -> Any:
    services, _settings, _folder = make_services(tmp_path)
    file_services.configure(services)
    yield services
    file_services.reset()


async def _chat_turn_with_upload(
    engine: AsyncEngine, workspace_id: UUID, user_id: UUID, upload_id: str, name: str
) -> UUID:
    """A chat session whose first user message carries the upload chip (as the chat stores it)."""
    session_id = uuid4()
    async with tenant_session(engine, workspace_id, user_id) as session:
        session.add(
            ChatSession(
                id=session_id, workspace_id=workspace_id, user_id=user_id, title="Budget deck"
            )
        )
        await session.flush()
        session.add(
            ChatMessage(
                id=uuid4(),
                workspace_id=workspace_id,
                session_id=session_id,
                sender="user",
                content="Here is my budget",
                modality="text",
                metadata_json={"attachments": [{"id": upload_id, "name": name, "kind": "text"}]},
            )
        )
        await session.flush()  # pending objects are discarded when the session closes
    return session_id


async def _steps(
    engine: AsyncEngine, workspace_id: UUID, user_id: UUID, task_id: UUID
) -> list[Any]:
    async with tenant_session(engine, workspace_id, user_id) as session:
        rows = await session.execute(
            select(Subtask).where(Subtask.task_id == task_id, Subtask.workspace_id == workspace_id)
        )
        return list(rows.scalars().all())


def _agent(step: Any) -> str:
    return str(getattr(step.assigned_agent, "value", step.assigned_agent))


@pytest.mark.parametrize("follow_up", ["make ppt of this", "haan"])
async def test_a_follow_up_turn_uses_the_upload_sent_earlier_in_the_chat(
    app_engine: AsyncEngine, fresh_tenants: Any, files: Any, tmp_path: Path, follow_up: str
) -> None:
    tenant = fresh_tenants[0]
    upload = files.uploads.save(tenant.workspace_id, tenant.user_id, "budget.txt", BUDGET.encode())
    session_id = await _chat_turn_with_upload(
        app_engine, tenant.workspace_id, tenant.user_id, upload.id, "budget.txt"
    )
    service = TaskSubmissionService(engine=app_engine, gateway=None, queue=SubtaskQueue())

    submitted = await service.submit(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        idempotency_key=f"chat-test-{uuid4().hex}",
        instruction=follow_up,
        title=None,
        session_id=session_id,
        source="chat",
    )

    assert submitted.is_new is True
    steps = await _steps(app_engine, tenant.workspace_id, tenant.user_id, submitted.task_id)
    assert steps, "the task has steps"
    for step in steps:
        assert step.input_data.get("input_files") == [upload.id]  # the file is on every step
    document = next(s for s in steps if _agent(s) == "document")
    assert "research" not in {_agent(s) for s in steps}  # no web search for a file request

    # What the worker does for the document step: the attachment's text joins its inputs.
    attached = await attachment_dependency_outputs(
        tenant.workspace_id, list(document.input_data["input_files"])
    )
    context = {
        **document.input_data,
        "description": document.description,
        "dependency_outputs": attached,
        "file_type": "pptx",
    }
    gateway = CapturingGateway()
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    from app.tasks.queue import SubtaskJob

    job = SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=submitted.task_id,
        subtask_id=document.id,
        assigned_agent="document",
    )
    await support._document_context(job, context)
    assert BUDGET in gateway.prompts[0]  # the builder read the user's own file


async def test_an_explicit_upload_id_is_stored_on_the_steps(
    app_engine: AsyncEngine, fresh_tenants: Any, files: Any
) -> None:
    tenant = fresh_tenants[0]
    upload = files.uploads.save(tenant.workspace_id, tenant.user_id, "budget.txt", BUDGET.encode())
    service = TaskSubmissionService(engine=app_engine, gateway=None, queue=SubtaskQueue())

    submitted = await service.submit(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        idempotency_key=f"chat-test-{uuid4().hex}",
        instruction="make ppt of this",
        title=None,
        session_id=None,
        source="api",
        input_files=[upload.id],
    )

    assert submitted.is_new is True
    steps = await _steps(app_engine, tenant.workspace_id, tenant.user_id, submitted.task_id)
    assert all(s.input_data.get("input_files") == [upload.id] for s in steps)


async def test_a_follow_up_that_names_no_file_gets_no_attachment(
    app_engine: AsyncEngine, fresh_tenants: Any, files: Any
) -> None:
    tenant = fresh_tenants[0]
    upload = files.uploads.save(tenant.workspace_id, tenant.user_id, "budget.txt", BUDGET.encode())
    session_id = await _chat_turn_with_upload(
        app_engine, tenant.workspace_id, tenant.user_id, upload.id, "budget.txt"
    )
    service = TaskSubmissionService(engine=app_engine, gateway=None, queue=SubtaskQueue())

    submitted = await service.submit(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        idempotency_key=f"chat-test-{uuid4().hex}",
        instruction="Research the weather in Paris this week",
        title=None,
        session_id=session_id,
        source="chat",
    )

    steps = await _steps(app_engine, tenant.workspace_id, tenant.user_id, submitted.task_id)
    assert steps and all("input_files" not in (s.input_data or {}) for s in steps)
