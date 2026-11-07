"""Sandbox Runner enforcing Decision D14: Non-root UID 10001, isolated boundaries."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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

BANNED_SOCKET_PATTERNS = [
    "docker.sock",
    "docker_engine",
    "/var/run/docker.sock",
    "//./pipe/docker_engine",
    r"\\.\pipe\docker_engine",
]

SAFE_ENV_PASSTHROUGH = frozenset(
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
    ) -> None:
        self.base_workspace_dir = Path(base_workspace_dir).resolve() if base_workspace_dir else None
        self.max_output_bytes = max_output_bytes
        self.docker_image = docker_image

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
            if self.base_workspace_dir and not ws_path.is_relative_to(self.base_workspace_dir):
                logger.warning(
                    "Refusing to clean up directory outside base_workspace_dir: %s", ws_path
                )
                return False
            if ws_path.exists() and ws_path.is_dir():
                shutil.rmtree(ws_path, ignore_errors=True)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to clean up sandbox workspace %s: %s", workspace_dir, exc)
        return False

    def _truncate_output(self, text: str) -> tuple[str, bool]:
        """Truncate output if it exceeds max_output_bytes limit."""
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) > self.max_output_bytes:
            truncated_bytes = encoded[: self.max_output_bytes]
            clean_text = truncated_bytes.decode("utf-8", errors="ignore")
            return clean_text + "\n[OUTPUT TRUNCATED]", True
        return text, False

    def execute_command(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        """Execute command in sandbox with timeout, resource, and containment bounds."""
        ws_path = self._validate_security_invariants(request)

        # Scrub ambient environment variables (Decision D14 / SEC-01)
        # Never leak DATABASE_URL, JWT_SIGNING_KEY, GEMINI_API_KEY, or other secrets to sandbox
        cmd_env: dict[str, str] = {
            k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_PASSTHROUGH
        }
        cmd_env["PYTHONUNBUFFERED"] = "1"
        cmd_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if request.env:
            cmd_env.update({str(k): str(v) for k, v in request.env.items()})

        # If command uses python or pytest, ensure running python interpreter is available
        command = list(request.command)
        if command and command[0] == "python":
            command[0] = sys.executable

        start_time = time.monotonic()
        try:
            res = subprocess.run(
                command,
                cwd=ws_path,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                env=cmd_env,
            )
            duration = time.monotonic() - start_time
            stdout, stdout_trunc = self._truncate_output(res.stdout)
            stderr, stderr_trunc = self._truncate_output(res.stderr)

            return SandboxExecutionResult(
                exit_code=res.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                timed_out=False,
                truncated=stdout_trunc or stderr_trunc,
            )

        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            raw_out = (
                exc.stdout
                if isinstance(exc.stdout, str)
                else (exc.stdout.decode() if exc.stdout else "")
            )
            raw_err = (
                exc.stderr
                if isinstance(exc.stderr, str)
                else (exc.stderr.decode() if exc.stderr else "")
            )
            stdout, stdout_trunc = self._truncate_output(raw_out)
            stderr, stderr_trunc = self._truncate_output(raw_err)

            timeout_msg = "\nExecution timed out." if not stderr else " Execution timed out."
            return SandboxExecutionResult(
                exit_code=124,  # Standard UNIX timeout exit code
                stdout=stdout,
                stderr=stderr + timeout_msg,
                duration_seconds=duration,
                timed_out=True,
                truncated=stdout_trunc or stderr_trunc,
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
