"""Deterministic Verification Schemas for Generated Deliverables."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class VerificationReport(BaseModel):
    """Structured report returned by all deterministic document verifiers."""

    file_type: Literal["xlsx", "docx", "pptx", "pdf"]
    status: Literal["verified", "failed"]
    sha256: str
    size_bytes: int
    checks_passed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    # Findings that do not fail the file (for example a slide count that differs from the stated
    # one: count policy, the file is delivered with a note).
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def compute_file_sha256(path: Path | str) -> str:
    """Compute the SHA-256 hexadecimal digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()
