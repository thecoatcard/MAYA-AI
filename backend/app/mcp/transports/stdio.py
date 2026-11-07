"""Subprocess-based stdio transport for local MCP servers (Decision D11 & D14).

Uses robust thread-bridged subprocess I/O compatible across all operating systems
and Windows event loop policies (both SelectorEventLoop and ProactorEventLoop).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from itertools import count
from typing import Any

from app.mcp.transports.base import (
    BaseTransport,
    MCPError,
    MCPProcessCrashedError,
    MCPTimeoutError,
    MCPTransportError,
)

logger = logging.getLogger(__name__)


class StdioTransport(BaseTransport):
    """Subprocess stdio transport with isolated JSON-RPC 2.0 framing and stream separation."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = list(args) if args else []
        self.env = dict(env) if env else {}
        self.cwd = cwd
        self._process: subprocess.Popen[str] | None = None
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._req_counter = count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Spawn the child process with scrubbed environment and captured stdio streams."""
        if self._process is not None and self._process.poll() is None:
            return

        self._closing = False
        self._loop = asyncio.get_running_loop()

        # Environment scrubbing: inherit only safe system variables and injected secrets
        clean_env: dict[str, str] = {}
        for var_name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH"):
            val = os.environ.get(var_name)
            if val is not None:
                clean_env[var_name] = val
        clean_env.update(self.env)
        # Decision D14: Ensure Docker socket or host root privileges are never passed
        clean_env.pop("DOCKER_HOST", None)

        cmd_list = [self.command, *self.args]
        try:
            self._process = await asyncio.to_thread(
                subprocess.Popen,
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line-buffered
                env=clean_env,
                cwd=self.cwd,
            )
        except Exception as err:
            raise MCPTransportError(
                f"Failed to spawn MCP subprocess '{self.command}': {err}"
            ) from err

        # Launch background readers for stdout and stderr
        self._reader_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    def is_alive(self) -> bool:
        """Check if process is currently running."""
        return self._process is not None and self._process.poll() is None

    async def _read_stdout_loop(self) -> None:
        """Read newline-delimited JSON-RPC messages from stdout and fulfill futures."""
        if self._process is None or self._process.stdout is None:
            return

        stdout = self._process.stdout
        while not self._closing:
            try:
                line_str = await asyncio.to_thread(stdout.readline)
                if not line_str:
                    # EOF hit: child process closed stdout or exited
                    break

                line_str = line_str.strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("MCP subprocess emitted non-JSON output on stdout: %s", line_str)
                    continue

                req_id = msg.get("id")
                if req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            err_payload = msg["error"]
                            err_msg = err_payload.get("message", "Unknown JSON-RPC error")
                            fut.set_exception(MCPError(err_msg))
                        else:
                            fut.set_result(msg.get("result", {}))
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error("Error in MCP stdout reader loop: %s", err)
                break

        # Process exited or pipe closed - fail all pending futures
        if not self._closing:
            exit_code = self._process.poll() if self._process else "unknown"
            crash_err = MCPProcessCrashedError(
                f"MCP subprocess '{self.command}' terminated unexpectedly "
                f"with exit code {exit_code}"
            )
            for fut in list(self._pending_requests.values()):
                if not fut.done():
                    fut.set_exception(crash_err)
            self._pending_requests.clear()

    async def _read_stderr_loop(self) -> None:
        """Read and log stderr lines without corrupting JSON-RPC stream."""
        if self._process is None or self._process.stderr is None:
            return

        stderr = self._process.stderr
        while not self._closing:
            try:
                line_str = await asyncio.to_thread(stderr.readline)
                if not line_str:
                    break
                line_str = line_str.strip()
                if line_str:
                    logger.debug("MCP subprocess [%s stderr]: %s", self.command, line_str)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def _sync_write(self, text_data: str) -> None:
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.write(text_data)
            self._process.stdin.flush()

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request frame and await its matching response."""
        if not self.is_alive() or self._process is None or self._process.stdin is None:
            raise MCPTransportError("Cannot send request; MCP subprocess is not running.")

        req_id = next(self._req_counter)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        encoded = json.dumps(payload, separators=(",", ":")) + "\n"

        loop = self._loop or asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = future

        async with self._write_lock:
            try:
                await asyncio.to_thread(self._sync_write, encoded)
            except Exception as err:
                self._pending_requests.pop(req_id, None)
                raise MCPTransportError(f"Failed writing to MCP stdin: {err}") from err

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            raise MCPTimeoutError(
                f"MCP request '{method}' timed out after {timeout} seconds."
            ) from None
        finally:
            self._pending_requests.pop(req_id, None)

    async def stop(self) -> None:
        """Gracefully terminate child process with SIGTERM, then SIGKILL on timeout."""
        self._closing = True
        if self._process is None:
            return

        proc = self._process
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=5.0)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "MCP subprocess '%s' did not terminate in 5.0s; issuing kill.", self.command
                    )
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
            except Exception:
                pass

        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
