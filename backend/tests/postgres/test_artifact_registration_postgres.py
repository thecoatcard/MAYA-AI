"""A website's file types never fail a task: artifact registration on REAL PostgreSQL.

Live run: a project step produced ``zip`` / ``html`` / ``css`` / ``js`` artifacts, the CHECK
``file_artifacts_file_type_check`` (pdf, xlsx, docx, pptx, md, csv, other) rejected the flush, and
the session was poisoned (``PendingRollbackError`` at the next outbox write), failing a good task.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import FileArtifact, SubtaskAgent
from app.db.tenant import tenant_session
from app.services.artifact_service import db_file_type
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event
from app.tasks.worker import _register_artifacts
from tests.postgres.test_agent_fleet_postgres import _job, _seed
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

SHA = "a" * 64


def _entry(name: str, file_type: str, sha: str = SHA) -> dict[str, object]:
    return {
        "storage_uri": f"/tmp/{name}",
        "file_name": name,
        "file_type": file_type,
        "size_bytes": 10,
        "sha256": sha,
    }


def test_every_produced_type_maps_to_the_allowed_set() -> None:
    allowed = {"pdf", "xlsx", "docx", "pptx", "md", "csv", "other"}
    for produced in ("zip", "html", "css", "js", "json", "txt", "py", ".ZIP", "", None):
        assert db_file_type(produced, "x.zip") in allowed
    assert db_file_type("zip") == "other" and db_file_type("PDF") == "pdf"
    assert db_file_type("markdown") == "md" and db_file_type(None, "notes.md") == "md"


@pytest.mark.asyncio
async def test_website_artifacts_register_and_a_bad_row_never_poisons_the_session(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, sub = await _seed(app_engine, tenant, SubtaskAgent.DOCUMENT, "Build a website")
    job = _job(tenant, task, sub, "document")
    proof = {
        **_entry("site.zip", "zip"),
        "status": "verified",
        "extra_artifacts": [
            _entry("index.html", "html"),
            _entry("style.css", "css"),
            _entry("script.js", "js"),
            _entry("broken.js", "js", sha="b" * 200),  # CHAR(64) overflow: a DB error
            _entry("README.md", "md"),
        ],
    }
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        warnings = await _register_artifacts(session, job, tenant.user_id, proof)
        # The session is still usable: the outbox write that used to raise PendingRollbackError.
        await write_outbox_event(
            session=session,
            workspace_id=tenant.workspace_id,
            task_id=task.id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={"ok": True},
        )
    assert len(warnings) == 1 and "broken.js" in warnings[0]

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        rows = list(
            (
                await session.execute(select(FileArtifact).where(FileArtifact.task_id == task.id))
            ).scalars()
        )
    by_name = {r.file_name: r.file_type for r in rows}
    assert by_name == {
        "site.zip": "other",
        "index.html": "other",
        "style.css": "other",
        "script.js": "other",
        "README.md": "md",
    }
