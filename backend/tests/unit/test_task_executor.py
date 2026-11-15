"""Unit tests for execute_subtask_with_agent and verified artifact generation."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.gateway.model_gateway import ModelGateway
from app.tasks.executor import execute_subtask_with_agent, generate_notes_pdf
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError, SubtaskExecutionError


class _FakeGateway:
    """Stands in for ModelGateway; no network."""

    def __init__(
        self, text: str = "Real model output.", citations: list[Any] | None = None
    ) -> None:
        self.text = text
        self.citations = citations or []
        self.prompts: list[str] = []

    async def generate_text(self, **kwargs: Any) -> Any:
        self.prompts.append(kwargs["contents"][0])
        return SimpleNamespace(text=self.text, citations=self.citations)


class _FailingGateway:
    async def generate_text(self, **kwargs: Any) -> Any:
        raise RuntimeError("gemini unavailable")


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
        res = await execute_subtask_with_agent(
            job,
            input_data,
            gateway=cast(ModelGateway, _FakeGateway()),  # test fake standing in for the gateway
            storage_root=tmp_dir,
        )

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
    gateway = _FakeGateway(citations=[{"title": "JEP 444", "url": "https://openjdk.org/jeps/444"}])
    res = await execute_subtask_with_agent(job, input_data, gateway=gateway)  # type: ignore[arg-type]

    assert "result" in res
    assert "proof" in res
    assert res["result"]["agent"] == "research"
    assert "citations" in res["result"]
    assert len(res["result"]["citations"]) >= 1
    assert res["proof"]["search_engine"] == "google_search"
    assert res["proof"]["citations_count"] >= 1


def _job(agent: str = "research") -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent=agent,
    )


@pytest.mark.asyncio
async def test_gateway_error_fails_instead_of_canned_success() -> None:
    with pytest.raises(SubtaskExecutionError, match="gemini unavailable"):
        await execute_subtask_with_agent(
            _job(),
            {"description": "Compile Java notes"},
            gateway=_FailingGateway(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_missing_gateway_is_non_retryable_failure() -> None:
    with pytest.raises(NonRetryableSubtaskError):
        await execute_subtask_with_agent(_job(), {"description": "anything"})


@pytest.mark.asyncio
async def test_text_output_proof_is_honestly_unverified() -> None:
    res = await execute_subtask_with_agent(
        _job("coding"),
        {"description": "Explain recursion"},
        gateway=_FakeGateway(text="Recursion is..."),  # type: ignore[arg-type]
    )
    assert res["proof"]["status"] == "unverified"
    assert len(res["proof"]["content_sha256"]) == 64
    assert isinstance(res["result"], dict)


@pytest.mark.asyncio
async def test_approval_token_never_reaches_the_prompt() -> None:
    marker = "placeholder-" + "x" * 12  # built at runtime; deliberately not token-shaped
    gateway = _FakeGateway()
    await execute_subtask_with_agent(
        _job("coding"),
        {"description": "Do it", "approval_token": marker, "nested": {"api_key": "k"}},
        gateway=gateway,  # type: ignore[arg-type]
    )
    assert marker not in gateway.prompts[0]
    assert "api_key" not in gateway.prompts[0]
