"""Pydantic schemas and dataclasses for Sandbox Execution (Decision D14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxCommandRequest:
    """Request specification for running a command in a sandboxed execution context."""

    command: list[str]
    workspace_dir: Path | str
    timeout_seconds: float = 60.0
    env: dict[str, str] | None = None
    extra_mounts: list[str] = field(default_factory=list)
    network_disabled: bool = True
    read_only_root: bool = True
    uid: int = 10001
    gid: int = 10001
    memory_limit: str = "2g"
    cpu_quota: float = 2.0


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    """Result payload of a sandboxed execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    truncated: bool = False
