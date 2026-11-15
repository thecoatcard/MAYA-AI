"""Structured outputs of the model steps (lenient: lists are clamped in code, not rejected)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    question: str = ""
    queries: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    topic: str = ""
    language: str = ""
    questions: list[SubQuestion] = Field(default_factory=list)


class SourceNotes(BaseModel):
    relevant: bool = True
    facts: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    stance: str = ""
    date: str | None = None


class Finding(BaseModel):
    claim: str = ""
    sources: list[int] = Field(default_factory=list)


class Position(BaseModel):
    claim: str = ""
    sources: list[int] = Field(default_factory=list)


class Contradiction(BaseModel):
    topic: str = ""
    positions: list[Position] = Field(default_factory=list)


class Synthesis(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)
