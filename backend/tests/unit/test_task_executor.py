"""Unit tests for execute_subtask_with_agent and verified artifact generation."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from app.tasks.executor import execute_subtask_with_agent, generate_notes_pdf
from app.tasks.queue import SubtaskJob


@pytest.mark.asyncio
async def test_generate_notes_pdf_creates_valid_pdf() -> None:
    with TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / "test_notes.pdf"
        content = (
            "### Section 1: Intro\n\n"
            "Java runs on JVM.\n\n"
            "### Section 2: OOP\n\n"
            "Classes and objects."
        )
        generate_notes_pdf(pdf_path, "Java Fundamentals Guide", content)
        assert pdf_path.is_file()
        assert pdf_path.stat().st_size > 500


@pytest.mark.asyncio
async def test_execute_subtask_generates_verified_pdf_deliverable() -> None:
    with TemporaryDirectory() as tmp_dir:
        job = SubtaskJob(
            workspace_id=uuid4(),
            user_id=uuid4(),
            task_id=uuid4(),
            subtask_id=uuid4(),
            assigned_agent="document",
        )
        input_data = {
            "description": "Compile comprehensive Java programming notes into PDF study guide",
        }
        res = await execute_subtask_with_agent(job, input_data, storage_root=tmp_dir)

        assert "result" in res
        assert "proof" in res
        proof = res["proof"]
        assert proof["status"] == "verified"
        assert proof["file_type"] == "pdf"
        assert proof["size_bytes"] > 0
        assert len(proof["sha256"]) == 64
        assert Path(proof["storage_uri"]).is_file()  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_execute_subtask_research_with_google_search_citations() -> None:
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )
    input_data = {
        "description": "Gather and research Java 21 concurrency features",
    }
    res = await execute_subtask_with_agent(job, input_data)

    assert "result" in res
    assert "proof" in res
    assert res["result"]["agent"] == "research"
    assert "citations" in res["result"]
    assert len(res["result"]["citations"]) >= 1
    assert res["proof"]["search_engine"] == "google_search"
    assert res["proof"]["citations_count"] >= 1
