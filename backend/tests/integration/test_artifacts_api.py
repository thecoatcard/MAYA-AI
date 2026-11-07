"""Comprehensive integration tests for Artifacts API and multi-tenant delivery."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth.postgres import PostgresAuthStore
from app.automation.docx_engine import create_document
from app.automation.pdf_engine import create_pdf
from app.automation.pptx_engine import create_presentation
from app.automation.xlsx_engine import create_spreadsheet
from app.core.config import Settings
from app.db.tenant import tenant_session
from app.main import create_app
from app.services.artifact_service import ArtifactService
from app.tasks.queue import SubtaskQueue
from tests.support.auth import build_harness
from tests.support.postgres import scoped


@pytest.fixture
def test_settings(migrated_database: Any) -> Settings:
    from tests.support.settings import make_settings

    return make_settings(
        database_url=migrated_database.app_url.render_as_string(hide_password=False),
        jwt_signing_key="j" * 32,
        otp_hmac_key="o" * 32,
    )


@pytest.mark.asyncio
async def test_artifacts_api_full_lifecycle(
    app_engine: Any,
    test_settings: Settings,
    tmp_path: Any,
) -> None:
    """Test full artifact lifecycle: generation, verification, storage, download, and RLS."""
    harness = build_harness(PostgresAuthStore(app_engine))
    app = create_app(test_settings, auth_service=harness.service)
    app.state.auth_service = harness.service
    app.state.settings = test_settings
    app.state.resources = SimpleNamespace(
        engine=app_engine,
        settings=test_settings,
        subtask_queue=SubtaskQueue(),
    )

    # 1. Onboard Tenant A (Alice)
    cmd_a = await harness.register_and_verify()
    tokens_a = await harness.login(cmd_a)
    claims_a = harness.tokens.decode_access(tokens_a.access_token)
    ws_a = claims_a.workspace_id
    user_a = claims_a.user_id
    headers_a = {"Authorization": f"Bearer {tokens_a.access_token}"}

    # 2. Onboard Tenant B (Bob) for cross-tenant testing
    cmd_b = await harness.register_and_verify()
    tokens_b = await harness.login(cmd_b)
    headers_b = {"Authorization": f"Bearer {tokens_b.access_token}"}

    task_id_a = uuid4()
    session_id_a = uuid4()

    # Seed task for Tenant A
    async with scoped(app_engine, ws_a) as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, workspace_id, user_id, title) "
                "VALUES (:s_id, :ws, :user, 'Session A')"
            ),
            {"s_id": session_id_a, "ws": ws_a, "user": user_a},
        )
        await conn.execute(
            text(
                "INSERT INTO tasks ("
                "  id, workspace_id, session_id, user_id, idempotency_key, title, user_instruction"
                ") VALUES (:t_id, :ws, :s_id, :user, :ikey, 'Generate Reports', 'Create artifacts')"
            ),
            {
                "t_id": task_id_a,
                "ws": ws_a,
                "s_id": session_id_a,
                "user": user_a,
                "ikey": uuid4().hex,
            },
        )
        await conn.commit()

    # 3. Generate documents using Phase 10 engines
    xlsx_path = tmp_path / "invoice.xlsx"
    create_spreadsheet(
        [
            {
                "title": "Sheet1",
                "headers": ["Item", "Cost"],
                "rows": [["Cloud", 100], ["License", 200]],
                "formulas": {"B4": "=SUM(B2:B3)"},
            }
        ],
        xlsx_path,
    )

    docx_path = tmp_path / "summary.docx"
    create_document(
        [
            {"type": "heading1", "text": "Report"},
            {"type": "paragraph", "text": "Content"},
        ],
        docx_path,
        title="Doc Summary",
    )

    pptx_path = tmp_path / "deck.pptx"
    create_presentation(
        [
            {"layout": "title", "title": "Pitch"},
            {"layout": "bullets", "title": "Points", "bullets": ["First", "Second"]},
        ],
        pptx_path,
    )

    pdf_path = tmp_path / "report.pdf"
    create_pdf(
        "Quarterly PDF",
        [
            {"type": "heading1", "text": "Overview"},
            {"type": "paragraph", "text": "Verified PDF text layer."},
        ],
        pdf_path,
    )

    # 4. Store artifacts via ArtifactService
    art_service = ArtifactService(storage_root=tmp_path / "storage")

    async with tenant_session(app_engine, ws_a, user_a) as session:
        art_xlsx = await art_service.store_artifact(
            session,
            workspace_id=ws_a,
            task_id=task_id_a,
            user_id=user_a,
            source_path=xlsx_path,
            file_type="xlsx",
        )
        art_docx = await art_service.store_artifact(
            session,
            workspace_id=ws_a,
            task_id=task_id_a,
            user_id=user_a,
            source_path=docx_path,
            file_type="docx",
        )
        art_pptx = await art_service.store_artifact(
            session,
            workspace_id=ws_a,
            task_id=task_id_a,
            user_id=user_a,
            source_path=pptx_path,
            file_type="pptx",
        )
        art_pdf = await art_service.store_artifact(
            session,
            workspace_id=ws_a,
            task_id=task_id_a,
            user_id=user_a,
            source_path=pdf_path,
            file_type="pdf",
        )
        await session.commit()

        assert art_xlsx.verification_status == "verified"
        assert art_docx.verification_status == "verified"
        assert art_pptx.verification_status == "verified"
        assert art_pdf.verification_status == "verified"

        art_id_xlsx = art_xlsx.id
        art_sha_xlsx = art_xlsx.sha256

    # Override app's default service storage root to use test storage
    from app.api.v1 import artifacts

    artifacts.artifact_service = art_service

    # 5. Test API Endpoints
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 5a. List artifacts for Tenant A
        res = await client.get("/api/v1/artifacts", headers=headers_a)
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 4
        assert any(it["file_type"] == "xlsx" for it in items)
        assert any(it["file_type"] == "pdf" for it in items)

        # 5b. Get metadata for XLSX artifact
        res = await client.get(f"/api/v1/artifacts/{art_id_xlsx}/metadata", headers=headers_a)
        assert res.status_code == 200
        meta = res.json()
        assert meta["id"] == str(art_id_xlsx)
        assert meta["file_name"] == "invoice.xlsx"
        assert meta["verification_status"] == "verified"
        assert "zero_formula_errors" in meta["verification_report"]["checks_passed"]

        # 5c. Download artifact
        res = await client.get(f"/api/v1/artifacts/{art_id_xlsx}", headers=headers_a)
        assert res.status_code == 200
        assert (
            res.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert 'attachment; filename="invoice.xlsx"' in res.headers["content-disposition"]
        assert res.headers["x-artifact-sha256"] == art_sha_xlsx
        assert res.headers["x-verification-status"] == "verified"

        # Verify content integrity: downloaded bytes match SHA-256
        downloaded_bytes = res.content
        assert hashlib.sha256(downloaded_bytes).hexdigest() == art_sha_xlsx

        # 6. Cross-Tenant Security Test (Tenant B cannot access Tenant A's artifact)
        res_cross = await client.get(f"/api/v1/artifacts/{art_id_xlsx}", headers=headers_b)
        assert res_cross.status_code == 404
        assert res_cross.json()["error"] == "artifact_not_found"

        res_cross_meta = await client.get(
            f"/api/v1/artifacts/{art_id_xlsx}/metadata", headers=headers_b
        )
        assert res_cross_meta.status_code == 404
        assert res_cross_meta.json()["error"] == "artifact_not_found"

        # Tenant B lists artifacts -> empty list
        res_cross_list = await client.get("/api/v1/artifacts", headers=headers_b)
        assert res_cross_list.status_code == 200
        assert len(res_cross_list.json()) == 0
