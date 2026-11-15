"""End-to-End Acceptance Tests for the 5 MVP Workflows (Phase 15, MAYA_AI_FINAL_PLAN.md §17).

Workflows automated:
- Workflow 1: Daily Briefing via Calendar & Notion (PMAgent) -> verified briefing.
- Workflow 3: GitHub Issue -> Root Cause Analysis -> Sandbox Fix -> Gated PR (CodingAgent).
- Workflow 5: Deep Competitive Research -> Verified PDF (ResearchAgent, DocumentAgent).
- Workflow 6: PDF Invoices -> Formula-Verified XLSX Spreadsheet (DocumentAgent).
- Workflow 7: Executive Brief -> 8-Slide PowerPoint Presentation without overflow (DocumentAgent).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentFleetRegistry
from app.agents.schemas import SpecialistAgentRole
from app.agents.specialized.coding import CodingAgent
from app.agents.specialized.document import DocumentAgent
from app.agents.specialized.pm import PMAgent
from app.agents.specialized.research import ResearchAgent
from app.automation.verifiers.pdf_verifier import verify_pdf
from app.automation.verifiers.pptx_verifier import verify_pptx
from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.db.models import Subtask, SubtaskStatus, Task
from app.mcp.connectors.google_search import GoogleSearchResult
from app.mcp.sanitizer import SanitizationResult
from app.sandbox.runner import SandboxRunner
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker
from tests.support.approval import TEST_JWT_KEY, approve_paused_subtask


@pytest.mark.asyncio
async def test_workflow_1_daily_briefing_e2e() -> None:
    """Workflow 1: Daily Briefing via Calendar & Notion (PMAgent)."""
    calendar_service = MagicMock()
    calendar_service.list_events = AsyncMock(
        return_value=[
            {
                "id": "cal-101",
                "summary": "Executive Strategy Sync",
                "start": {"dateTime": "2026-11-07T09:00:00Z"},
            },
            {
                "id": "cal-102",
                "summary": "Product Roadmap Review",
                "start": {"dateTime": "2026-11-07T14:00:00Z"},
            },
        ]
    )
    notion_connector = MagicMock()
    notion_connector.query_database = AsyncMock(
        return_value=[
            {
                "id": "notion-201",
                "properties": {
                    "Name": {"title": [{"plain_text": "Phase 15 Verification & Hardening"}]}
                },
            },
            {
                "id": "notion-202",
                "properties": {
                    "Name": {"title": [{"plain_text": "SOC2 Readiness Audit Deliverables"}]}
                },
            },
        ]
    )

    pm_agent = PMAgent(
        calendar_service=calendar_service,
        notion_connector=notion_connector,
        notion_database_id="tasks-db",
    )
    fleet = AgentFleetRegistry()
    fleet.register_agent(SpecialistAgentRole.PM, pm_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)
    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Synthesize Daily Executive Briefing",
        assigned_agent="pm",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={"date": "2026-11-07"},
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Daily Morning Briefing",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str or "subtasks.id" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )

    final_status = await worker.execute_job(session=session, job=job)
    assert final_status == SubtaskStatus.COMPLETED
    assert subtask.status == SubtaskStatus.COMPLETED
    result_text = str(subtask.output_result)
    assert "Executive Strategy Sync" in result_text
    assert "Phase 15 Verification & Hardening" in result_text
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["events_reviewed"] == 2
    assert subtask.verification_proof["tasks_reviewed"] == 2


@pytest.mark.asyncio
async def test_workflow_3_github_issue_sandbox_fix_gated_pr_e2e(tmp_path: Path) -> None:
    """Workflow 3: GitHub Issue -> Root Cause Analysis -> Sandbox Fix -> Gated PR (CodingAgent)."""
    sandbox = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "repo_sandbox"
    workspace.mkdir()

    # Create reproducible bug: calculate_vat returns 0 instead of 0.20
    (workspace / "tax.py").write_text(
        "def calculate_vat(subtotal: float) -> float:\n    return 0.0\n",
        encoding="utf-8",
    )
    (workspace / "test_tax.py").write_text(
        "import tax\n\ndef test_vat():\n    assert tax.calculate_vat(100.0) == 20.0\n",
        encoding="utf-8",
    )

    github_connector = MagicMock()
    github_connector.create_pull_request = AsyncMock(
        return_value={"number": 142, "html_url": "https://github.com/maya-ai/maya/pull/142"}
    )

    coding_agent = CodingAgent(
        sandbox_runner=sandbox,
        github_connector=github_connector,
    )
    fleet = AgentFleetRegistry()
    fleet.register_agent(SpecialistAgentRole.CODING, coding_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)
    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Fix tax calculation and submit PR",
        assigned_agent="coding",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_tax.py"],
            "patch_file": "tax.py",
            "patch_content": (
                "def calculate_vat(subtotal: float) -> float:\n    return subtotal * 0.20\n"
            ),
            "has_valid_approval_token": False,
        },
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Resolve Issue #142: Incorrect VAT",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str or "subtasks.id" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="coding",
    )

    # 1. Unapproved execution -> pauses at safety gate (AWAITING_APPROVAL)
    status_unapproved = await worker.execute_job(session=session, job=job)
    assert status_unapproved == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.status == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["retest_passed"] is True

    # 2. Approved execution -> completes and returns PR URL
    # A real signed approval token is verified and claimed; a bare input flag no longer works.
    approve_paused_subtask(session, subtask, task)
    status_approved = await worker.execute_job(session=session, job=job)
    assert status_approved == SubtaskStatus.COMPLETED
    assert str(subtask.status) == str(SubtaskStatus.COMPLETED)
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["pr_url"] == "https://github.com/maya-ai/maya/pull/142"


@pytest.mark.asyncio
async def test_workflow_5_competitive_research_to_verified_pdf_e2e(tmp_path: Path) -> None:
    """Workflow 5: Competitive Research -> Verified PDF Report with text layer."""
    # 1. ResearchAgent executes web search
    mock_search = MagicMock()
    mock_search.search = AsyncMock(
        return_value=GoogleSearchResult(
            query="Enterprise AI agent market differentiators 2026",
            text="Enterprise AI market is adopting deterministic verifiers rapidly.",
            citations=[
                {
                    "title": "Enterprise AI Report 2026",
                    "url": "https://analyst.example.com/ai-2026",
                    "snippet": "Market growth at 38% CAGR driven by deterministic verifiers.",
                },
                {
                    "title": "Gartner AI Magic Quadrant",
                    "url": "https://gartner.example.com/magic-quadrant",
                    "snippet": "Multi-agent systems with row-level security lead adoption.",
                },
            ],
            sanitized_result=SanitizationResult(
                clean_text="Enterprise AI market is adopting deterministic verifiers rapidly.",
                wrapped_output="Enterprise AI market is adopting deterministic verifiers rapidly.",
                has_injection_suspicions=False,
                suspicions=(),
            ),
        )
    )

    research_agent = ResearchAgent(google_search_connector=mock_search)
    document_agent = DocumentAgent(artifacts_base_dir=tmp_path)

    fleet = AgentFleetRegistry()
    fleet.register_agent(SpecialistAgentRole.RESEARCH, research_agent)
    fleet.register_agent(SpecialistAgentRole.DOCUMENT, document_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)
    workspace_id = uuid4()
    task_id = uuid4()

    # Step A: Research subtask
    subtask_research_id = uuid4()
    subtask_research = Subtask(
        id=subtask_research_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Gather competitor market analysis",
        assigned_agent="research",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={"query": "Enterprise AI agent market differentiators 2026"},
    )

    # Step B: Document generation subtask
    subtask_doc_id = uuid4()
    pdf_sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "1. Market Executive Summary"},
        {
            "type": "paragraph",
            "text": (
                "The enterprise AI workforce market represents $50B in addressable spend. "
                "MAYA AI provides verified multi-agent task execution with strict "
                "row-level security."
            ),
        },
        {
            "type": "table",
            "headers": ["Competitor", "Architecture", "Safety Gating"],
            "rows": [
                ["Legacy RPA", "Script-based", "None (Static credentials)"],
                ["Chatbot Wrappers", "LLM Prompting", "Best-effort system prompt"],
                ["MAYA AI", "LangGraph DAG + MCP", "Deterministic Gate & RFC 8785"],
            ],
        },
        {"type": "page_break"},
        {"type": "heading1", "text": "2. Security Architecture & Invariants"},
        {
            "type": "paragraph",
            "text": (
                "Decisions D4 and D5 enforce cryptographic approval tokens and atomic database "
                "consumption, ensuring zero unauthorized mutations."
            ),
        },
    ]

    subtask_doc = Subtask(
        id=subtask_doc_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Generate Verified Competitive Analysis PDF Report",
        assigned_agent="document",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={
            "filename": "competitive_analysis_2026.pdf",
            "doc_type": "pdf",
            "title": "Enterprise AI Market Intelligence Report",
            "subtitle": "Confidential Strategic Assessment",
            "sections": pdf_sections,
        },
    )

    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Competitive Intelligence Briefing",
        status="in_progress",
    )

    current_subtask = subtask_research

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str or "subtasks.id" in stmt_str:
            mock_result.scalars.return_value.first.return_value = current_subtask
            mock_result.scalars.return_value.all.return_value = [current_subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    # Execute Research
    job_research = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_research_id,
        assigned_agent="research",
    )
    status_r = await worker.execute_job(session=session, job=job_research)
    assert status_r == SubtaskStatus.COMPLETED
    assert subtask_research.verification_proof is not None
    assert subtask_research.verification_proof["citations_count"] == 2

    # Switch to Document subtask
    current_subtask = subtask_doc

    # Execute PDF Document Generation
    job_doc = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_doc_id,
        assigned_agent="document",
    )
    status_d = await worker.execute_job(session=session, job=job_doc)
    assert status_d == SubtaskStatus.COMPLETED

    # Verify generated PDF with deterministic verifier
    generated_pdf = tmp_path / str(workspace_id) / "competitive_analysis_2026.pdf"
    assert generated_pdf.is_file()

    report = verify_pdf(generated_pdf)
    assert report.status == "verified"
    assert report.metadata["total_pages"] >= 2
    assert any("text_layer" in c for c in report.checks_passed)
    assert len(report.errors) == 0


@pytest.mark.asyncio
async def test_workflow_6_pdf_invoices_to_formula_verified_xlsx_e2e(tmp_path: Path) -> None:
    """Workflow 6: PDF Invoices -> Formula-Verified XLSX spreadsheet without #REF! errors."""
    document_agent = DocumentAgent(artifacts_base_dir=tmp_path)
    fleet = AgentFleetRegistry()
    fleet.register_agent(SpecialistAgentRole.DOCUMENT, document_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)
    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    sheets = [
        {
            "title": "Vendor Invoices Q3",
            "headers": ["Invoice Number", "Vendor Name", "Subtotal", "Tax", "Total Amount"],
            "rows": [
                ["INV-9001", "Cloud Infrastructure Corp", 15200.00, 3040.00, 18240.00],
                ["INV-9002", "Cybersecurity Audits Ltd", 8500.00, 1700.00, 10200.00],
                ["INV-9003", "Data Pipelines Inc", 12100.00, 2420.00, 14520.00],
                ["INV-9004", "Enterprise Telephony AG", 4300.00, 860.00, 5160.00],
            ],
            "formulas": {
                "C6": "=SUM(C2:C5)",
                "D6": "=SUM(D2:D5)",
                "E6": "=SUM(E2:E5)",
                "E7": "=AVERAGE(E2:E5)",
            },
            "number_formats": {
                3: "$#,##0.00",
                4: "$#,##0.00",
                5: "$#,##0.00",
            },
        }
    ]

    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Synthesize Invoices into Verified Spreadsheet",
        assigned_agent="document",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={
            "filename": "q3_invoices_summary.xlsx",
            "doc_type": "xlsx",
            "sheets": sheets,
        },
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Invoice Extraction and Financial Reconciliation",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str or "subtasks.id" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="document",
    )

    final_status = await worker.execute_job(session=session, job=job)
    assert final_status == SubtaskStatus.COMPLETED

    generated_xlsx = tmp_path / str(workspace_id) / "q3_invoices_summary.xlsx"
    assert generated_xlsx.is_file()

    # Deterministic verifier: zero formula errors (#REF!, #VALUE!, etc.)
    report = verify_xlsx(generated_xlsx)
    assert report.status == "verified"
    assert report.metadata["formulas_checked"] == 4
    assert "zero_formula_errors" in report.checks_passed
    assert len(report.errors) == 0


@pytest.mark.asyncio
async def test_workflow_7_executive_brief_to_8_slide_pptx_e2e(tmp_path: Path) -> None:
    """Workflow 7: Executive Brief -> 8-Slide PPTX without bounding-box text overflow."""
    document_agent = DocumentAgent(artifacts_base_dir=tmp_path)
    fleet = AgentFleetRegistry()
    fleet.register_agent(SpecialistAgentRole.DOCUMENT, document_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)
    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    slides: list[dict[str, Any]] = [
        {
            "layout": "title",
            "title": "MAYA AI Digital Employee",
            "subtitle": "Executive Board Presentation & Operations Review",
        },
        {
            "layout": "cards",
            "title": "Strategic Capabilities",
            "cards": [
                {
                    "title": "Multi-Agent System",
                    "body": "Specialist agents coordinate via LangGraph DAGs.",
                },
                {
                    "title": "Deterministic Verification",
                    "body": "Every PDF, XLSX, and PPTX artifact verified before delivery.",
                },
                {"title": "Zero-Trust Security", "body": "Strict row-level security and RLS."},
            ],
        },
        {
            "layout": "metrics",
            "title": "Key Platform Metrics",
            "metrics": [
                {"value": "<850ms", "label": "p95 First-Audio Latency"},
                {"value": "<150ms", "label": "Barge-In Response"},
                {"value": "100%", "label": "Security Gate Enforcement"},
            ],
        },
        {
            "layout": "bullets",
            "title": "Multi-Agent Specialist Fleet",
            "bullets": [
                "ResearchAgent for verified web discovery with Google-grounded citations",
                "CodingAgent operating in non-root Docker sandboxes",
                "DocumentAgent generating validated office deliverables",
                "PMAgent synthesizing calendars and task boards",
            ],
        },
        {
            "layout": "bullets",
            "title": "Enterprise Security Invariants",
            "bullets": [
                "Decision D4: Four-tier tool permissions with deny-by-default",
                "Decision D5: Single-use HMAC-SHA256 signed approval tokens",
                "Decision D8: Non-blocking asynchronous worker execution",
                "Atomic PostgreSQL consumption to prevent TOCTOU races",
            ],
        },
        {
            "layout": "bullets",
            "title": "Infrastructure & Data Architecture",
            "bullets": [
                "PostgreSQL 17 with native Row-Level Security on all tenant tables",
                "Redis 7.4 streams for transactional outbox pattern",
                "MinIO S3-compatible encrypted artifact store",
                "Model Gateway with multi-provider fallback and cost controls",
            ],
        },
        {
            "layout": "bullets",
            "title": "Milestone Achievements",
            "bullets": [
                "Phases 01 through 14 implemented and validated locally",
                "100% test passing rate across integration and unit suites",
                "Deterministic verifiers for documents and code execution",
            ],
        },
        {
            "layout": "bullets",
            "title": "Strategic Roadmap & Next Steps",
            "bullets": [
                "Phase 15 Verification and Hardening completed",
                "Phase 16 Production deployment and monitoring initiation",
                "Global multi-tenant enterprise rollout",
            ],
        },
    ]

    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Generate 8-Slide Executive Pitch Deck",
        assigned_agent="document",
        status=SubtaskStatus.PENDING,
        retry_count=0,
        input_data={
            "filename": "executive_pitch_deck.pptx",
            "doc_type": "pptx",
            "slides": slides,
        },
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Executive Briefing Presentation",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str or "subtasks.id" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="document",
    )

    final_status = await worker.execute_job(session=session, job=job)
    assert final_status == SubtaskStatus.COMPLETED

    generated_pptx = tmp_path / str(workspace_id) / "executive_pitch_deck.pptx"
    assert generated_pptx.is_file()

    # Deterministic verifier: exactly 8 slides, zero bounding-box text overflow
    report = verify_pptx(generated_pptx)
    assert report.status == "verified"
    assert report.metadata["total_slides"] == 8
    assert any("within_canvas_bounds" in c for c in report.checks_passed)
    assert len(report.errors) == 0
