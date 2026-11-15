"""Sandbox Runner enforcing Decision D14: Non-root UID 10001, isolated boundaries."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.sandbox.schemas import SandboxCommandRequest, SandboxExecutionResult

__all__ = [
    "BANNED_SOCKET_PATTERNS",
    "SAFE_ENV_PASSTHROUGH",
    "SandboxCommandRequest",
    "SandboxExecutionResult",
    "SandboxRunner",
    "SecurityViolationError",
]

logger = logging.getLogger(__name__)

BANNED_SOCKET_PATTERNS: list[str] = [
    "docker.sock",
    "docker_engine",
    "/var/run/docker.sock",
    "//./pipe/docker_engine",
    r"\\.\pipe\docker_engine",
]

SAFE_ENV_PASSTHROUGH: frozenset[str] = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PYTHONPATH",
        "LANG",
        "LC_ALL",
    }
)


class SecurityViolationError(Exception):
    """Raised when an execution violates sandbox security boundaries (e.g. mounting docker.sock)."""


class SandboxRunner:
    """Fixed-function sandbox runner executing commands under strict isolation."""

    def __init__(
        self,
        base_workspace_dir: Path | str | None = None,
        max_output_bytes: int = 1048576,  # 1 MB default
        docker_image: str = "maya-sandbox:latest",
        use_docker: bool | None = None,
    ) -> None:
        self.base_workspace_dir = Path(base_workspace_dir).resolve() if base_workspace_dir else None
        self.max_output_bytes = max_output_bytes
        self.docker_image = docker_image
        self.use_docker = use_docker
        self._docker_available_cache: dict[str, bool] = {}

    def is_docker_available(self) -> bool:
        """Check whether Docker execution should be used."""
        if self.use_docker is not None:
            return bool(self.use_docker)

        env_val = os.environ.get("MAYA_SANDBOX_USE_DOCKER", "").strip().lower()
        if env_val in ("1", "true", "yes", "on"):
            return True
        if env_val in ("0", "false", "no", "off"):
            return False

        if self.docker_image in self._docker_available_cache:
            return self._docker_available_cache[self.docker_image]

        if not shutil.which("docker"):
            self._docker_available_cache[self.docker_image] = False
            return False

        # Verify the docker image exists locally and daemon is reachable
        try:
            res = subprocess.run(
                ["docker", "image", "inspect", self.docker_image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            is_avail = res.returncode == 0
        except Exception:
            is_avail = False

        self._docker_available_cache[self.docker_image] = is_avail
        return is_avail

    def _validate_security_invariants(self, request: SandboxCommandRequest) -> Path:
        """Verify Decision D14 security invariants before running command."""
        # 1. Check for banned Docker socket patterns in mounts
        for mount in request.extra_mounts:
            mount_str = str(mount).lower()
            for banned in BANNED_SOCKET_PATTERNS:
                if banned in mount_str:
                    raise SecurityViolationError(
                        f"Mounting Docker socket is strictly prohibited (Decision D14): {mount}"
                    )
            raise SecurityViolationError(
                f"Mounting extra directories is prohibited (Decision D14): {mount}"
            )

        # 2. Check for banned Docker socket in workspace_dir
        ws_str = str(request.workspace_dir).lower()
        for banned in BANNED_SOCKET_PATTERNS:
            if banned in ws_str:
                raise SecurityViolationError(
                    f"Workspace references banned Docker socket: {request.workspace_dir}"
                )

        # 3. Check for banned Docker socket in command
        for cmd_part in request.command:
            part_str = str(cmd_part).lower()
            for banned in BANNED_SOCKET_PATTERNS:
                if banned in part_str:
                    raise SecurityViolationError(
                        f"Command references banned Docker socket (Decision D14): {cmd_part}"
                    )

        # 4. Check for banned Docker socket in env keys and values
        if request.env:
            for k, v in request.env.items():
                k_str = str(k).lower()
                v_str = str(v).lower()
                for banned in BANNED_SOCKET_PATTERNS:
                    if banned in k_str or banned in v_str:
                        raise SecurityViolationError(
                            f"Environment variable references banned Docker socket: {k}"
                        )

        # 5. Verify workspace is confined to base_workspace_dir
        ws_path = Path(request.workspace_dir).resolve()
        if self.base_workspace_dir:
            try:
                if not ws_path.is_relative_to(self.base_workspace_dir):
                    raise SecurityViolationError(
                        f"Workspace '{ws_path}' is outside permitted base directory."
                    )
            except ValueError:
                raise SecurityViolationError(
                    f"Workspace '{ws_path}' is outside permitted base directory."
                ) from None

        if not ws_path.exists():
            ws_path.mkdir(parents=True, exist_ok=True)

        return ws_path

    def cleanup_workspace(self, workspace_dir: Path | str) -> bool:
        """Clean up an ephemeral workspace directory securely."""
        try:
            ws_path = Path(workspace_dir).resolve()
            if self.base_workspace_dir:
                if not ws_path.is_relative_to(self.base_workspace_dir):
                    logger.warning(
                        "Refusing to clean up directory outside base_workspace_dir: %s", ws_path
                    )
                    return False
                if ws_path == self.base_workspace_dir:
                    logger.warning(
                        "Refusing to clean up base_workspace_dir root itself: %s", ws_path
                    )
                    return False
            # Prevent deleting filesystem root
            if ws_path == ws_path.parent:
                logger.warning("Refusing to clean up filesystem root: %s", ws_path)
                return False
            if ws_path.exists() and ws_path.is_dir():
                shutil.rmtree(ws_path, ignore_errors=True)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to clean up sandbox workspace %s: %s", workspace_dir, exc)
        return False

    async def cleanup_workspace_async(self, workspace_dir: Path | str) -> bool:
        """Asynchronously clean up an ephemeral workspace directory securely."""
        return await asyncio.to_thread(self.cleanup_workspace, workspace_dir)

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        """Truncate output if it exceeds max_output_bytes limit."""
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) > self.max_output_bytes:
            truncated_bytes = encoded[: self.max_output_bytes]
            clean_text = truncated_bytes.decode("utf-8", errors="ignore")
            return clean_text + "\n[OUTPUT TRUNCATED]", True
        return text, False

    def _build_docker_argv(
        self,
        request: SandboxCommandRequest,
        ws_path: Path,
        container_name: str,
    ) -> list[str]:
        """Build the hardened 'docker run' command line enforcing Decision D14."""
        argv: list[str] = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
        ]

        if request.network_disabled:
            argv.extend(["--network", "none"])

        if request.read_only_root:
            argv.append("--read-only")
            argv.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])

        argv.extend(
            [
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                f"{request.uid}:{request.gid}",
                "--memory",
                str(request.memory_limit),
                "--cpus",
                str(request.cpu_quota),
                "--mount",
                f"type=bind,source={ws_path},target=/workspace",
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTHONUNBUFFERED=1",
                "--env",
                "HOME=/tmp",
            ]
        )

        if request.env:
            for k, v in request.env.items():
                argv.extend(["--env", f"{k}={v}"])

        argv.append(self.docker_image)

        # Normalize command inside container (python executable should refer to container's python)
        cmd = list(request.command)
        if cmd:
            first = cmd[0].lower()
            if (
                first.endswith("python.exe")
                or first.endswith("python")
                or (sys.executable and first == sys.executable.lower())
            ):
                cmd[0] = "python"
        argv.extend(cmd)

        return argv

    async def _kill_process_tree(self, process: asyncio.subprocess.Process) -> None:
        """Terminate process and all its children cleanly."""
        if process.returncode is not None:
            return
        pid = process.pid
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except Exception:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        with contextlib.suppress(Exception):
            await process.wait()

    async def _kill_docker_container(self, container_name: str) -> None:
        """Best-effort stop/kill of a docker container on timeout."""
        with contextlib.suppress(Exception):
            kill_proc = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(kill_proc.wait(), timeout=5.0)

    async def _execute_async_process(
        self,
        argv: list[str],
        cwd: str | None,
        env: dict[str, str] | None,
        timeout_seconds: float,
        spawn_kwargs: dict[str, Any],
        container_name: str | None = None,
    ) -> SandboxExecutionResult:
        """Core async execution with stream reading, timeout enforcement, and output truncation."""
        start_time = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **spawn_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            duration = time.monotonic() - start_time
            return SandboxExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {exc}",
                duration_seconds=duration,
                timed_out=False,
                truncated=False,
            )

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_bytes_len = 0
        stderr_bytes_len = 0
        stdout_truncated = False
        stderr_truncated = False

        async def read_stream(
            reader: asyncio.StreamReader | None,
            chunks: list[bytes],
            is_stdout: bool,
        ) -> None:
            nonlocal stdout_bytes_len, stderr_bytes_len, stdout_truncated, stderr_truncated
            if reader is None:
                return
            limit = self.max_output_bytes + 4096
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                if is_stdout:
                    if stdout_bytes_len < limit:
                        chunks.append(chunk)
                        stdout_bytes_len += len(chunk)
                    else:
                        stdout_truncated = True
                else:
                    if stderr_bytes_len < limit:
                        chunks.append(chunk)
                        stderr_bytes_len += len(chunk)
                    else:
                        stderr_truncated = True

        stdout_task = asyncio.create_task(read_stream(process.stdout, stdout_chunks, True))
        stderr_task = asyncio.create_task(read_stream(process.stderr, stderr_chunks, False))

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            await self._kill_process_tree(process)
            if container_name:
                await self._kill_docker_container(container_name)

        # Drain output streams with bounded grace period
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(stdout_task, stderr_task), timeout=1.0)

        duration = time.monotonic() - start_time
        raw_stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        raw_stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        stdout, stdout_trunc = self._truncate_output(raw_stdout)
        stderr, stderr_trunc = self._truncate_output(raw_stderr)

        if timed_out:
            timeout_msg = "\nExecution timed out." if not stderr else " Execution timed out."
            return SandboxExecutionResult(
                exit_code=124,  # Standard UNIX timeout exit code
                stdout=stdout,
                stderr=stderr + timeout_msg,
                duration_seconds=duration,
                timed_out=True,
                truncated=stdout_trunc or stderr_trunc or stdout_truncated or stderr_truncated,
            )

        exit_code = process.returncode if process.returncode is not None else 1
        return SandboxExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=False,
            truncated=stdout_trunc or stderr_trunc or stdout_truncated or stderr_truncated,
        )

    async def _execute_docker(
        self, request: SandboxCommandRequest, ws_path: Path
    ) -> SandboxExecutionResult:
        """Execute command inside Docker container."""
        container_name = f"maya-sbx-{uuid.uuid4().hex[:12]}"
        argv = self._build_docker_argv(request, ws_path, container_name)
        spawn_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            spawn_kwargs["start_new_session"] = True

        return await self._execute_async_process(
            argv=argv,
            cwd=None,
            env=None,  # docker client inherits parent env to reach docker daemon
            timeout_seconds=request.timeout_seconds,
            spawn_kwargs=spawn_kwargs,
            container_name=container_name,
        )

    async def _execute_local(
        self, request: SandboxCommandRequest, ws_path: Path
    ) -> SandboxExecutionResult:
        """Safe local async execution fallback when Docker is not configured or unavailable."""
        # Scrub ambient environment variables (Decision D14 / SEC-01)
        # Never leak DATABASE_URL, JWT_SIGNING_KEY, GEMINI_API_KEY, or other secrets to sandbox
        cmd_env: dict[str, str] = {
            k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_PASSTHROUGH
        }
        cmd_env["PYTHONUNBUFFERED"] = "1"
        cmd_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if request.env:
            cmd_env.update({str(k): str(v) for k, v in request.env.items()})

        # If command uses python, use current running python interpreter
        command = list(request.command)
        if command and command[0] == "python":
            command[0] = sys.executable

        spawn_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            spawn_kwargs["start_new_session"] = True

        return await self._execute_async_process(
            argv=command,
            cwd=str(ws_path),
            env=cmd_env,
            timeout_seconds=request.timeout_seconds,
            spawn_kwargs=spawn_kwargs,
            container_name=None,
        )

    async def _run_async_internal(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        """Internal worker executing either Docker or local execution."""
        if not request.command:
            return SandboxExecutionResult(
                exit_code=1,
                stdout="",
                stderr="No command specified.",
                duration_seconds=0.0,
                timed_out=False,
                truncated=False,
            )

        ws_path = self._validate_security_invariants(request)

        if self.is_docker_available():
            return await self._execute_docker(request, ws_path)
        return await self._execute_local(request, ws_path)

    def _run_in_proactor_thread(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        """Helper to run async execution in a ProactorEventLoop on Windows."""
        loop = asyncio.ProactorEventLoop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self._run_async_internal(request))
        finally:
            loop.close()

    async def run_async(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        """Asynchronously execute a command in the sandbox with isolation and containment bounds."""
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            if not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ())):
                return await asyncio.to_thread(self._run_in_proactor_thread, request)
        return await self._run_async_internal(request)

    def run(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        """Synchronously execute a sandbox command (backwards-compatible with run_async)."""
        if sys.platform == "win32":
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(self._run_in_proactor_thread, request).result()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self._run_async_internal(request)).result()

        return asyncio.run(self._run_async_internal(request))

    # Backwards-compatible aliases
    execute_command = run
    execute_command_async = run_async
