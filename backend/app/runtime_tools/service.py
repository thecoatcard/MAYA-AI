"""Runtime-tool orchestration: propose, approve, run. No chat or HTTP knowledge in here.

The one rule that matters: ``run`` starts NOTHING unless (1) the executor reports an isolated
runtime, (2) the tool version has a valid grant bound to the hash of its source and capabilities,
and (3) the arguments match the tool's input schema. Otherwise it returns a refusal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from app.runtime_tools import schema_check
from app.runtime_tools.executor import (
    HARNESS_SOURCE,
    SANDBOX_UNAVAILABLE_MESSAGE,
    SandboxJob,
    ToolExecutor,
)
from app.runtime_tools.registry import (
    MODE_REUSABLE,
    RegistryError,
    ToolRecord,
    ToolRegistry,
    validate_name,
)

logger = logging.getLogger(__name__)

MAX_RESULT_BYTES: Final = 20 * 1024
MAX_STDERR_CHARS: Final = 1500
MAX_INPUT_FILES: Final = 5
MAX_INPUT_FILE_BYTES: Final = 25 * 1024 * 1024
MAX_OUTPUT_FILES: Final = 5
MAX_OUTPUT_FILE_BYTES: Final = 10 * 1024 * 1024
MAX_OUTPUT_TOTAL_BYTES: Final = 25 * 1024 * 1024
# Output types the chat may deliver. Extension AND leading bytes must agree where a signature
# exists (see ``_SIGNATURES``). SVG and HTML are excluded: they can carry script.
OUTPUT_TYPES: Final = frozenset(
    {"csv", "json", "txt", "md", "xlsx", "docx", "pdf", "png", "jpg", "jpeg"}
)
_SIGNATURES: Final = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "pdf": (b"%PDF-",),
    "xlsx": (b"PK\x03\x04",),
    "docx": (b"PK\x03\x04",),
}
_SAFE_NAME: Final = re.compile(r"[^A-Za-z0-9._-]+")
RUNTIME_DIR: Final = "runtime_tools"


@dataclass(frozen=True, slots=True)
class InputFile:
    """A file the user's conversation already holds, resolved by the caller (never by the model)."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class OutputFile:
    name: str
    data: bytes
    file_type: str


@dataclass(slots=True)
class RunOutcome:
    status: str  # ok | failed | timeout | refused
    message: str = ""
    result_text: str = ""
    result_truncated: bool = False
    stderr_tail: str = ""
    files: list[OutputFile] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class Proposal:
    record: ToolRecord
    created: bool
    grant: str  # none | once | reusable
    reusable: bool
    step_args: dict[str, Any]


class RunBudget:
    """Runs left in one chat turn. ``take`` is the only way to spend one."""

    def __init__(self, max_runs: int) -> None:
        self.max_runs = max_runs
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.max_runs:
            return False
        self.used += 1
        return True


def _safe_file_name(name: str, index: int) -> str:
    base = _SAFE_NAME.sub("_", Path(name.replace("\\", "/")).name).strip("._") or "file"
    return f"{index}_{base[:80]}"


class RuntimeToolService:
    def __init__(
        self,
        *,
        storage_root: Path | str,
        executor: ToolExecutor,
        timeout_seconds: float = 60.0,
        max_runs_per_turn: int = 3,
        scratch_parent: Path | str | None = None,
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.max_runs_per_turn = max_runs_per_turn
        self._scratch_parent = Path(scratch_parent) if scratch_parent else None

    def new_budget(self) -> RunBudget:
        return RunBudget(self.max_runs_per_turn)

    def registry(self, workspace_id: UUID | str) -> ToolRegistry:
        if re.fullmatch(r"[0-9A-Fa-f-]{8,40}", str(workspace_id)) is None:
            raise RegistryError("invalid workspace", "invalid_workspace")
        return ToolRegistry(self.storage_root / str(workspace_id) / RUNTIME_DIR)

    # ------------------------------------------------------------------ search / propose

    def search(self, workspace_id: UUID | str, query: str) -> list[dict[str, Any]]:
        registry = self.registry(workspace_id)
        out: list[dict[str, Any]] = []
        for record in registry.search(query):
            out.append(
                {
                    "name": record.name,
                    "version": record.version,
                    "description": record.manifest.get("description", ""),
                    "input_schema": record.manifest.get("input_schema", {}),
                    "capabilities": record.capabilities,
                    "approval": registry.grant_state(record),
                }
            )
        return out

    def propose(
        self,
        workspace_id: UUID | str,
        user_id: UUID | str,
        *,
        name: str,
        description: str,
        source: str,
        input_schema: dict[str, Any],
        capabilities: object,
        reusable: bool,
    ) -> Proposal:
        """Validate and store a proposal, and build the exact payload the user will approve."""
        registry = self.registry(workspace_id)
        record, created = registry.create(
            name=name,
            description=description,
            source=source,
            input_schema=input_schema,
            capabilities=capabilities,
            created_by=str(user_id),
        )
        grant = registry.grant_state(record)
        network = bool(record.capabilities.get("network"))
        keep = bool(reusable) and not network
        step_args: dict[str, Any] = {
            "name": record.name,
            "version": record.version,
            "binding": record.binding,
            "description": record.manifest.get("description", ""),
            "source": record.source,
            "source_sha256": record.sha256,
            "input_schema": record.manifest.get("input_schema", {}),
            "capabilities": record.capabilities,
            "reuse": MODE_REUSABLE if keep else "once",
        }
        return Proposal(record, created, grant, keep, step_args)

    def approve(self, workspace_id: UUID | str, args: dict[str, Any]) -> str:
        """The approval-gated step: record the grant for EXACTLY the approved payload."""
        name = validate_name(args.get("name"))
        version = args.get("version")
        binding = str(args.get("binding") or "")
        if not isinstance(version, int) or isinstance(version, bool) or not binding:
            raise RegistryError("the approval payload is incomplete", "invalid_payload")
        registry = self.registry(workspace_id)
        record = registry.get(name, version)
        # What the user saw must be what is stored: re-check the shown source against the files.
        shown = args.get("source")
        if isinstance(shown, str) and shown != record.source:
            raise RegistryError(
                "the stored code differs from the approved code", "binding_mismatch"
            )
        reusable = args.get("reuse") == MODE_REUSABLE
        return registry.approve(name, version, binding=binding, reusable=reusable)

    # ------------------------------------------------------------------ run

    async def run(
        self,
        workspace_id: UUID | str,
        user_id: UUID | str,
        *,
        name: str,
        version: int | None,
        args: dict[str, Any],
        inputs: list[InputFile],
    ) -> RunOutcome:
        # 1. No isolated runtime: refuse BEFORE touching anything (a one-time grant is not spent).
        if not self.executor.available():
            return RunOutcome("refused", SANDBOX_UNAVAILABLE_MESSAGE)
        registry = self.registry(workspace_id)
        try:
            record = registry.get(validate_name(name), version)
        except RegistryError as error:
            return RunOutcome("refused", str(error))
        caps = record.capabilities
        if caps.get("network") and not getattr(self.executor, "supports_network", False):
            return RunOutcome(
                "refused",
                "this tool asks for network access, which the sandbox does not provide here",
            )
        if inputs and not caps.get("files_in"):
            return RunOutcome("refused", "this tool was not approved to read files")
        if len(inputs) > MAX_INPUT_FILES:
            return RunOutcome("refused", f"at most {MAX_INPUT_FILES} input files per run")
        if not isinstance(args, dict):
            return RunOutcome("refused", "args must be a JSON object")
        try:
            encoded = json.dumps(args)
        except (TypeError, ValueError):
            return RunOutcome("refused", "args must be plain JSON")
        if len(encoded.encode("utf-8")) > schema_check.MAX_ARGS_BYTES:
            return RunOutcome("refused", "args are too large")
        problems = schema_check.validate(args, record.manifest.get("input_schema"))
        if problems:
            return RunOutcome("refused", "invalid arguments: " + "; ".join(problems))
        # 2. Approval bound to the hash; a once-only grant is spent here, before the run.
        try:
            registry.consume(record)
        except RegistryError as error:
            return RunOutcome("refused", str(error))
        return await self._execute(registry, record, user_id, args, inputs)

    def _scratch(self) -> tempfile.TemporaryDirectory[str]:
        parent = self._scratch_parent
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="maya-rt-", dir=parent)

    async def _execute(
        self,
        registry: ToolRegistry,
        record: ToolRecord,
        user_id: UUID | str,
        args: dict[str, Any],
        inputs: list[InputFile],
    ) -> RunOutcome:
        with self._scratch() as scratch_name:
            scratch = Path(scratch_name)
            try:
                job = await asyncio.to_thread(self._prepare, scratch, record, args, inputs)
            except (OSError, ValueError) as error:
                return RunOutcome("refused", f"an input file could not be used: {error}")
            try:
                result = await asyncio.wait_for(
                    self.executor.run(job), timeout=self.timeout_seconds + 10
                )
            except TimeoutError:
                outcome = RunOutcome("timeout", "the tool ran out of time and was stopped")
                self._log(registry, record, user_id, 124, 0.0, 0, 0, True)
                return outcome
            except Exception as error:  # noqa: BLE001 - never leak internals to the model
                logger.warning("runtime_tool_executor_failed error=%s", type(error).__name__)
                return RunOutcome("failed", "the sandbox could not run the tool")
            outcome = await asyncio.to_thread(self._collect, job, record, result)
            self._log(
                registry,
                record,
                user_id,
                result.exit_code,
                result.duration_seconds,
                len(outcome.result_text.encode("utf-8")),
                len(outcome.files),
                result.timed_out,
            )
            return outcome

    def _log(
        self,
        registry: ToolRegistry,
        record: ToolRecord,
        user_id: UUID | str,
        exit_code: int,
        duration: float,
        output_bytes: int,
        files_out: int,
        timed_out: bool,
    ) -> None:
        try:
            registry.log_run(
                record,
                exit_code=exit_code,
                duration_seconds=duration,
                output_bytes=output_bytes,
                files_out=files_out,
                timed_out=timed_out,
                user_id=str(user_id),
            )
        except (OSError, RegistryError):
            logger.warning("runtime_tool_log_failed")

    def _prepare(
        self, scratch: Path, record: ToolRecord, args: dict[str, Any], inputs: list[InputFile]
    ) -> SandboxJob:
        code, work = scratch / "code", scratch / "work"
        (work / "in").mkdir(parents=True)
        (work / "out").mkdir()
        code.mkdir()
        (code / "tool.py").write_text(record.source, encoding="utf-8", newline="\n")
        (code / "harness.py").write_text(HARNESS_SOURCE, encoding="utf-8", newline="\n")
        copied: list[str] = []
        for index, item in enumerate(inputs, start=1):
            source = Path(item.path)
            # Originals are never mounted or modified: a COPY goes into the run's own folder.
            if source.is_symlink() or not source.is_file():
                raise ValueError("only regular files can be passed to a tool")
            if source.stat().st_size > MAX_INPUT_FILE_BYTES:
                raise ValueError("an input file is too large")
            target = work / "in" / _safe_file_name(item.name, index)
            shutil.copyfile(source, target)
            os.chmod(target, 0o444)
            copied.append(f"in/{target.name}")
        payload = dict(args)
        payload["input_files"] = copied  # set by MAYA; the model cannot choose paths
        (work / "args.json").write_text(json.dumps(payload), encoding="utf-8")
        with_open = 0o777
        for folder in (work, work / "out"):
            try:
                os.chmod(folder, with_open)  # the container user (UID 10001) must write here
            except OSError:  # pragma: no cover - platform dependent
                pass
        return SandboxJob(code, work, self.timeout_seconds, network=False)

    def _collect(self, job: SandboxJob, record: ToolRecord, result: Any) -> RunOutcome:
        outcome = RunOutcome(
            "ok" if result.exit_code == 0 and not result.timed_out else "failed",
            duration_seconds=float(result.duration_seconds),
        )
        if result.timed_out:
            outcome.status = "timeout"
            outcome.message = "the tool ran out of time and was stopped"
        elif result.exit_code != 0:
            outcome.message = f"the tool failed (exit code {result.exit_code})"
        stderr = str(result.stderr or "")
        outcome.stderr_tail = stderr[-MAX_STDERR_CHARS:]
        result_path = job.work_dir / "result.json"
        if outcome.status == "ok":
            try:
                if result_path.is_symlink():
                    raise OSError("not a regular file")
                raw = result_path.read_bytes()[: MAX_RESULT_BYTES + 1]
            except OSError:
                raw = b""
                outcome.status = "failed"
                outcome.message = "the tool returned no result"
            if len(raw) > MAX_RESULT_BYTES:
                outcome.result_truncated = True
                raw = raw[:MAX_RESULT_BYTES]
            outcome.result_text = raw.decode("utf-8", errors="replace")
        if outcome.status == "ok" and record.capabilities.get("files_out"):
            self._collect_files(job.work_dir / "out", outcome)
        elif outcome.status == "ok" and any((job.work_dir / "out").iterdir()):
            outcome.skipped_files.append("files were written but this tool has no file output")
        return outcome

    def _collect_files(self, out_dir: Path, outcome: RunOutcome) -> None:
        total = 0
        for path in sorted(out_dir.iterdir()):
            label = path.name[:80]
            if path.is_symlink() or not path.is_file():
                outcome.skipped_files.append(f"{label}: not a regular file")
                continue
            extension = path.suffix.lower().lstrip(".")
            if extension not in OUTPUT_TYPES:
                outcome.skipped_files.append(f"{label}: file type not allowed")
                continue
            if len(outcome.files) >= MAX_OUTPUT_FILES:
                outcome.skipped_files.append(f"{label}: too many output files")
                continue
            size = path.stat().st_size
            if size > MAX_OUTPUT_FILE_BYTES or total + size > MAX_OUTPUT_TOTAL_BYTES:
                outcome.skipped_files.append(f"{label}: too large")
                continue
            data = path.read_bytes()
            signatures = _SIGNATURES.get(extension)
            if signatures and not data.startswith(signatures):
                outcome.skipped_files.append(f"{label}: content does not match its type")
                continue
            clean = _SAFE_NAME.sub("_", path.name).strip("._") or f"output.{extension}"
            total += size
            outcome.files.append(OutputFile(clean[:100], data, extension))


__all__ = [
    "MAX_OUTPUT_FILE_BYTES",
    "MAX_RESULT_BYTES",
    "OUTPUT_TYPES",
    "InputFile",
    "OutputFile",
    "Proposal",
    "RunBudget",
    "RunOutcome",
    "RuntimeToolService",
]
