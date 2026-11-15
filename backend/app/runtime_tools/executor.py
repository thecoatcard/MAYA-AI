"""Where runtime-tool code runs: ONLY in an isolated container, never on the host.

What the existing ``app.sandbox.SandboxRunner`` is (read before reusing it): despite its name and
its README it does NOT start a container. ``execute_command`` calls ``subprocess.run`` with the
host's Python and merely scrubs the environment, so it provides no filesystem, network or resource
isolation. Model-written code must therefore never be given to it. This module adds a separate,
explicit path that keeps the fixed-function runner untouched:

* ``ToolExecutor`` is the interface (``available`` and ``run``); tests use a fake that implements
  it. A fake must never execute code on the host either.
* ``ContainerExecutor`` builds a hardened ``docker run`` command line: no network, read-only root
  filesystem, all capabilities dropped, no-new-privileges, non-root UID 10001, memory, CPU,
  process and file-size limits, a size-capped ``/tmp``, no environment passed in, and exactly two
  bind mounts: the tool code (read-only) and the per-run work folder (read-write, holding only the
  copied input files). It refuses the Docker socket (Decision D14) and any other mount.
* ``UnavailableExecutor`` is used when no sandbox image is configured. It never spawns anything.

There is no fallback: when the executor is not available the run is refused with
``SANDBOX_UNAVAILABLE_MESSAGE`` and nothing is started on the host.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from app.sandbox.runner import BANNED_SOCKET_PATTERNS, SecurityViolationError
from app.sandbox.schemas import SandboxExecutionResult

SANDBOX_UNAVAILABLE_MESSAGE: Final = "code tools need the sandbox, which is not available here"

CONTAINER_UID: Final = 10001
CONTAINER_MEMORY_MB: Final = 512
CONTAINER_CPUS: Final = 1.0
CONTAINER_PIDS: Final = 128
CONTAINER_FILE_SIZE_BYTES: Final = 64 * 1024 * 1024
CONTAINER_TMP_MB: Final = 64
MAX_CAPTURE_BYTES: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class SandboxJob:
    """One run. ``code_dir`` holds the tool and the harness (mounted read-only); ``work_dir`` holds
    ``args.json`` and ``in/`` going in and ``out/`` and ``result.json`` coming out."""

    code_dir: Path
    work_dir: Path
    timeout_seconds: float
    network: bool = False
    max_output_bytes: int = MAX_CAPTURE_BYTES


@runtime_checkable
class ToolExecutor(Protocol):
    def available(self) -> bool:
        """True only when an isolated runtime can really be used."""
        ...

    async def run(self, job: SandboxJob) -> SandboxExecutionResult: ...


class UnavailableExecutor:
    """No isolated runtime is configured. Starts nothing, ever."""

    def available(self) -> bool:
        return False

    async def run(self, job: SandboxJob) -> SandboxExecutionResult:  # pragma: no cover - guarded
        raise RuntimeError(SANDBOX_UNAVAILABLE_MESSAGE)


Spawn = Callable[[list[str], float, int], Awaitable[SandboxExecutionResult]]


async def _spawn_docker(
    argv: list[str], limit_seconds: float, max_bytes: int
) -> SandboxExecutionResult:
    """Start the ``docker`` CLIENT (the code itself runs inside the container). Replaced by a fake
    in every test; never used against the host Python."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=limit_seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        return SandboxExecutionResult(
            exit_code=124,
            stdout="",
            stderr="Execution timed out.",
            duration_seconds=loop.time() - started,
            timed_out=True,
        )
    text_out = out[:max_bytes].decode("utf-8", errors="replace")
    text_err = err[:max_bytes].decode("utf-8", errors="replace")
    return SandboxExecutionResult(
        exit_code=process.returncode if process.returncode is not None else 1,
        stdout=text_out,
        stderr=text_err,
        duration_seconds=loop.time() - started,
        truncated=len(out) > max_bytes or len(err) > max_bytes,
    )


class ContainerExecutor:
    """Runs the harness inside a container started from ``image`` (a locally available image).

    ``which`` and ``spawn`` are injectable so tests can prove what would be started without
    starting anything. The image must contain Python 3.12 and the allowed libraries; building it
    is an infrastructure change that needs the owner (see ADR-0027).
    """

    def __init__(
        self,
        image: str,
        *,
        which: Callable[[str], str | None] = shutil.which,
        spawn: Spawn | None = None,
    ) -> None:
        self.image = image.strip()
        self._which = which
        self._spawn: Spawn = spawn if spawn is not None else _spawn_docker

    def available(self) -> bool:
        return bool(self.image) and self._which("docker") is not None

    def build_argv(self, job: SandboxJob, name: str) -> list[str]:
        """The exact ``docker run`` command. Raises ``SecurityViolationError`` for a bad mount."""
        if job.network:
            raise SecurityViolationError(
                "network access for runtime tools is not supported: an egress-filtered network "
                "has not been provisioned (ADR-0027)"
            )
        mounts = (job.code_dir, job.work_dir)
        for mount in mounts:
            lowered = str(mount).lower()
            if any(pattern in lowered for pattern in BANNED_SOCKET_PATTERNS):
                raise SecurityViolationError(f"mounting a Docker socket is prohibited: {mount}")
            if "," in str(mount) or "=" in str(mount):
                raise SecurityViolationError("mount paths may not contain ',' or '='")
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{CONTAINER_UID}:{CONTAINER_UID}",
            "--memory",
            f"{CONTAINER_MEMORY_MB}m",
            "--memory-swap",
            f"{CONTAINER_MEMORY_MB}m",
            "--cpus",
            str(CONTAINER_CPUS),
            "--pids-limit",
            str(CONTAINER_PIDS),
            "--ulimit",
            f"fsize={CONTAINER_FILE_SIZE_BYTES}",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={CONTAINER_TMP_MB}m",  # noqa: S108
            "--mount",
            f"type=bind,source={job.code_dir},target=/code,readonly",
            "--mount",
            f"type=bind,source={job.work_dir},target=/work",
            "--workdir",
            "/work",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "HOME=/tmp",
            self.image,
            "python",
            "-I",
            "/code/harness.py",
        ]

    async def run(self, job: SandboxJob) -> SandboxExecutionResult:
        if not self.available():
            raise RuntimeError(SANDBOX_UNAVAILABLE_MESSAGE)
        name = f"maya-rt-{uuid.uuid4().hex[:16]}"
        argv = self.build_argv(job, name)
        # A little slack so the harness's own timeout handling is not the one that decides.
        result = await self._spawn(argv, job.timeout_seconds, job.max_output_bytes)
        if result.timed_out:
            # The client was killed on timeout; the container is stopped by name (best effort).
            with contextlib.suppress(Exception):
                await self._spawn(["docker", "kill", name], 10.0, 4096)
        return result


HARNESS_SOURCE: Final = '''\
"""Fixed harness (written by MAYA, not by the model). Runs /code/tool.py main(args)."""
import importlib.util
import json
import sys

with open("/work/args.json", encoding="utf-8") as handle:
    args = json.load(handle)
spec = importlib.util.spec_from_file_location("maya_tool", "/code/tool.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.main(args)
with open("/work/result.json", "w", encoding="utf-8") as handle:
    json.dump(result, handle, default=str)
sys.exit(0)
'''


__all__ = [
    "HARNESS_SOURCE",
    "SANDBOX_UNAVAILABLE_MESSAGE",
    "ContainerExecutor",
    "SandboxJob",
    "ToolExecutor",
    "UnavailableExecutor",
]
