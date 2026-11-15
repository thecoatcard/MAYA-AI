"""Fakes for runtime-tool tests. Nothing here executes model code, on the host or anywhere else.

``FakeExecutor`` implements ``ToolExecutor``: instead of running ``tool.py`` it calls a scripted
``behavior(job)`` that writes whatever the pretend tool "produced" into the job's work folder.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.runtime_tools.executor import SandboxJob
from app.sandbox.schemas import SandboxExecutionResult

Behavior = Callable[[SandboxJob], SandboxExecutionResult | None]

CHART_SOURCE = """\
import csv
import json


def main(args):
    with open(args["input_files"][0], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with open("out/chart.png", "wb") as handle:
        handle.write(b"fake")
    return {"rows": len(rows)}
"""

ECHO_SOURCE = """\
def main(args):
    return {"echo": args.get("text", "")}
"""

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image"


def ok_result(**fields: Any) -> SandboxExecutionResult:
    return SandboxExecutionResult(exit_code=0, stdout="", stderr="", duration_seconds=0.1, **fields)


class FakeExecutor:
    def __init__(self, behavior: Behavior | None = None, *, available: bool = True) -> None:
        self._available = available
        self.behavior = behavior
        self.jobs: list[SandboxJob] = []
        self.seen_inputs: list[list[str]] = []
        self.supports_network = False

    def available(self) -> bool:
        return self._available

    async def run(self, job: SandboxJob) -> SandboxExecutionResult:
        self.jobs.append(job)
        self.seen_inputs.append(sorted(p.name for p in (job.work_dir / "in").iterdir()))
        if self.behavior is not None:
            produced = self.behavior(job)
            if produced is not None:
                return produced
        return ok_result()


def write_result(job: SandboxJob, value: Any) -> None:
    (job.work_dir / "result.json").write_text(json.dumps(value), encoding="utf-8")


def echo_behavior(job: SandboxJob) -> SandboxExecutionResult | None:
    args = json.loads((job.work_dir / "args.json").read_text(encoding="utf-8"))
    write_result(job, {"echo": args.get("text", ""), "n_inputs": len(args["input_files"])})
    return None


def chart_behavior(job: SandboxJob) -> SandboxExecutionResult | None:
    write_result(job, {"rows": 2})
    (job.work_dir / "out" / "chart.png").write_bytes(PNG)
    return None
