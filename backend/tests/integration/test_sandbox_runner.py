"""Integration tests for Fixed-Function Sandbox Runner (Decision D14, Phase 09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.sandbox.runner import (
    SandboxCommandRequest,
    SandboxRunner,
    SecurityViolationError,
)


def test_sandbox_runner_forbids_docker_socket_mount(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "ws1"
    workspace.mkdir()

    prohibited_mounts = [
        "/var/run/docker.sock",
        "/var/run/docker.sock:/var/run/docker.sock",
        "//./pipe/docker_engine",
        "docker.sock",
    ]

    for bad_mount in prohibited_mounts:
        with pytest.raises(SecurityViolationError) as exc_info:
            runner.execute_command(
                SandboxCommandRequest(
                    command=["python", "--version"],
                    workspace_dir=workspace,
                    extra_mounts=[bad_mount],
                )
            )
        assert "docker socket" in str(exc_info.value).lower()


def test_sandbox_runner_executes_python_command(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "ws1"
    workspace.mkdir()

    # Create a small script
    script = workspace / "test_calc.py"
    script.write_text("print(2 + 2)\n", encoding="utf-8")

    result = runner.execute_command(
        SandboxCommandRequest(
            command=["python", "test_calc.py"],
            workspace_dir=workspace,
        )
    )

    assert result.exit_code == 0
    assert "4" in result.stdout.strip()
    assert result.timed_out is False


def test_sandbox_runner_timeout_enforcement(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "ws1"
    workspace.mkdir()

    # Sleep longer than timeout
    result = runner.execute_command(
        SandboxCommandRequest(
            command=["python", "-c", "import time; time.sleep(5)"],
            workspace_dir=workspace,
            timeout_seconds=0.2,
        )
    )

    assert result.timed_out is True
    assert result.exit_code != 0


def test_sandbox_runner_truncates_large_output(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path, max_output_bytes=5000)
    workspace = tmp_path / "ws1"
    workspace.mkdir()

    # Print 20,000 characters
    result = runner.execute_command(
        SandboxCommandRequest(
            command=["python", "-c", "print('A' * 20000)"],
            workspace_dir=workspace,
        )
    )

    assert len(result.stdout) <= 5200  # 5000 + truncation notice
    assert "[OUTPUT TRUNCATED]" in result.stdout


def test_sandbox_runner_blocks_traversal_outside_base(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path / "sandboxes")
    outside_dir = tmp_path / "outside_system"
    outside_dir.mkdir()

    with pytest.raises(SecurityViolationError):
        runner.execute_command(
            SandboxCommandRequest(
                command=["python", "--version"],
                workspace_dir=outside_dir,
            )
        )


def test_sandbox_runner_scrubs_ambient_environment_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision D14 & SEC-01: ambient host secrets must not leak to sandbox subprocess."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@127.0.0.1/db")
    monkeypatch.setenv("MAYA_JWT_SIGNING_KEY", "super-secret-production-key")

    runner = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "ws_secret"
    workspace.mkdir()

    script = workspace / "check_env.py"
    script.write_text(
        "import os\n"
        "print('DB:', os.environ.get('DATABASE_URL'))\n"
        "print('JWT:', os.environ.get('MAYA_JWT_SIGNING_KEY'))\n",
        encoding="utf-8",
    )

    res = runner.execute_command(
        SandboxCommandRequest(
            command=["python", "check_env.py"],
            workspace_dir=workspace,
        )
    )
    assert res.exit_code == 0
    assert "DB: None" in res.stdout
    assert "JWT: None" in res.stdout
    assert "super-secret-production-key" not in res.stdout


def test_sandbox_runner_detects_banned_socket_in_all_fields(tmp_path: Path) -> None:
    """Decision D14 & BUG-15: banned socket in any field raises SecurityViolation."""
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()

    # In workspace path
    with pytest.raises(SecurityViolationError, match="banned Docker socket"):
        runner.execute_command(
            SandboxCommandRequest(
                command=["python", "--version"], workspace_dir="/var/run/docker.sock"
            )
        )

    # In command argument
    with pytest.raises(SecurityViolationError, match="banned Docker socket"):
        runner.execute_command(
            SandboxCommandRequest(
                command=["curl", "--unix-socket", "/var/run/docker.sock", "http://localhost"],
                workspace_dir=ws,
            )
        )

    # In env variable
    with pytest.raises(SecurityViolationError, match="banned Docker socket"):
        runner.execute_command(
            SandboxCommandRequest(
                command=["python", "--version"],
                workspace_dir=ws,
                env={"DOCKER_HOST": "unix:///var/run/docker.sock"},
            )
        )


def test_sandbox_runner_cleanup_workspace(tmp_path: Path) -> None:
    """Test workspace cleanup functionality."""
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    ws = tmp_path / "ephemeral_ws"
    ws.mkdir()
    (ws / "artifact.txt").write_text("temporary data", encoding="utf-8")

    assert ws.exists()
    assert runner.cleanup_workspace(ws) is True
    assert not ws.exists()
