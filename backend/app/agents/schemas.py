"""Pydantic schemas and typed dataclasses for Specialist AI Agents (Phase 09)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SpecialistAgentRole(StrEnum):
    """Supported specialist agent roles."""

    RESEARCH = "research"
    CODING = "coding"
    DOCUMENT = "document"
    EMAIL = "email"
    PM = "pm"


class AgentExecutionStatus(StrEnum):
    """Execution status resulting from a specialist agent's subtask execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SpecialistAgentResult:
    """Standardized output produced by all specialist agents with verification proof."""

    agent_role: SpecialistAgentRole
    status: AgentExecutionStatus
    summary: str
    verification_proof: dict[str, Any]
    citations: list[dict[str, str]] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    requires_human_approval: bool = False
    approval_context: dict[str, Any] | None = None
