"""Unit tests for fixed-function SandboxRunner (Decision D14, async execution, Docker isolation)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.sandbox.runner import (
    BANNED_SOCKET_PATTERNS,
    SandboxCommandRequest,
    SandboxRunner,
    SecurityViolationError,
)


@pytest.mark.asyncio
async def test_run_async_basic_execution(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path, use_docker=False)
    workspace = tmp_path / "ws_async"
    workspace.mkdir()

    script = workspace / "hello.py"
    script.write_text("print('hello sandbox')\n", encoding="utf-8")

    result = await runner.run_async(
        SandboxCommandRequest(
            command=["python", "hello.py"],
            workspace_dir=workspace,
        )
    )

    assert result.exit_code == 0
    assert "hello sandbox" in result.stdout
    assert result.timed_out is False
    assert result.duration_seconds >= 0.0


def test_run_sync_and_execute_command_compatibility(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path, use_docker=False)
    workspace = tmp_path / "ws_sync"
    workspace.mkdir()

    script = workspace / "add.py"
    script.write_text("print(10 + 20)\n", encoding="utf-8")

    # test run()
    res1 = runner.run(
        SandboxCommandRequest(
            command=["python", "add.py"],
            workspace_dir=workspace,
        )
    )
    assert res1.exit_code == 0
    assert "30" in res1.stdout

    # test execute_command() backwards compatibility
    res2 = runner.execute_command(
        SandboxCommandRequest(
            command=["python", "add.py"],
            workspace_dir=workspace,
        )
    )
    assert res2.exit_code == 0
    assert "30" in res2.stdout


@pytest.mark.asyncio
async def test_execute_command_async_alias(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path, use_docker=False)
    workspace = tmp_path / "ws_alias"
    workspace.mkdir()

    res = await runner.execute_command_async(
        SandboxCommandRequest(
            command=["python", "-c", "print('async alias works')"],
            workspace_dir=workspace,
        )
    )
    assert res.exit_code == 0
    assert "async alias works" in res.stdout


@pytest.mark.asyncio
async def test_empty_command_handled_gracefully(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path, use_docker=False)
    res = await runner.run_async(
        SandboxCommandRequest(
            command=[],
            workspace_dir=tmp_path,
        )
    )
    assert res.exit_code == 1
    assert "No command specified" in res.stderr


def test_docker_argv_construction(tmp_path: Path) -> None:
    runner = SandboxRunner(
        base_workspace_dir=tmp_path,
        docker_image="custom-sandbox:v2",
        use_docker=True,
    )
    workspace = tmp_path / "ws_docker"
    workspace.mkdir()

    request = SandboxCommandRequest(
        command=["python", "script.py"],
        workspace_dir=workspace,
        timeout_seconds=45.0,
        env={"CUSTOM_VAR": "val123"},
        network_disabled=True,
        read_only_root=True,
        uid=10001,
        gid=10001,
        memory_limit="1g",
        cpu_quota=1.5,
    )

    argv = runner._build_docker_argv(request, workspace, "maya-sbx-test123")

    assert argv[0] == "docker"
    assert argv[1] == "run"
    assert "--rm" in argv
    assert "--name" in argv
    assert "maya-sbx-test123" in argv
    assert "--network" in argv
    assert "none" in argv
    assert "--read-only" in argv
    assert "--cap-drop" in argv
    assert "ALL" in argv
    assert "--security-opt" in argv
    assert "no-new-privileges" in argv
    assert "--user" in argv
    assert "10001:10001" in argv
    assert "--memory" in argv
    assert "1g" in argv
    assert "--cpus" in argv
    assert "1.5" in argv
    assert f"type=bind,source={workspace},target=/workspace" in argv
    assert "/workspace" in argv
    assert "--env" in argv
    assert "CUSTOM_VAR=val123" in argv
    assert "custom-sandbox:v2" in argv
    assert argv[-2:] == ["python", "script.py"]


@pytest.mark.asyncio
async def test_docker_execution_spawns_and_returns(tmp_path: Path) -> None:
    runner = SandboxRunner(
        base_workspace_dir=tmp_path,
        docker_image="maya-sandbox:latest",
        use_docker=True,
    )
    workspace = tmp_path / "ws_mock_docker"
    workspace.mkdir()

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.pid = 9999
    mock_process.wait.return_value = 0
    mock_process.stdout = AsyncMock()
    mock_process.stdout.read.side_effect = [b"docker run success\n", b""]
    mock_process.stderr = AsyncMock()
    mock_process.stderr.read.side_effect = [b""]

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        result = await runner.run_async(
            SandboxCommandRequest(
                command=["python", "test.py"],
                workspace_dir=workspace,
            )
        )

        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "docker"
        assert "run" in call_args
        assert result.exit_code == 0
        assert "docker run success" in result.stdout
        assert result.timed_out is False


@pytest.mark.asyncio
async def test_docker_timeout_kills_container(tmp_path: Path) -> None:
    runner = SandboxRunner(
        base_workspace_dir=tmp_path,
        docker_image="maya-sandbox:latest",
        use_docker=True,
    )
    workspace = tmp_path / "ws_docker_timeout"
    workspace.mkdir()

    mock_process = AsyncMock()
    mock_process.returncode = None
    mock_process.pid = 8888
    # Simulate wait raising TimeoutError
    mock_process.wait.side_effect = [TimeoutError(), 0]
    mock_process.stdout = AsyncMock()
    mock_process.stdout.read.side_effect = [b""]
    mock_process.stderr = AsyncMock()
    mock_process.stderr.read.side_effect = [b""]

    kill_calls: list[list[str]] = []

    async def fake_subprocess_exec(*args: str, **kwargs: object) -> AsyncMock:
        if args and args[0] == "docker" and len(args) > 1 and args[1] == "kill":
            kill_calls.append(list(args))
            kill_mock = AsyncMock()
            kill_mock.wait.return_value = 0
            return kill_mock
        return mock_process

    with (
        patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec),
        patch.object(runner, "_kill_process_tree", new_callable=AsyncMock) as mock_kill_tree,
    ):
        result = await runner.run_async(
            SandboxCommandRequest(
                command=["python", "slow.py"],
                workspace_dir=workspace,
                timeout_seconds=0.01,
            )
        )

        assert result.timed_out is True
        assert result.exit_code == 124
        assert "Execution timed out" in result.stderr
        assert mock_kill_tree.called
        assert len(kill_calls) == 1
        assert kill_calls[0][0] == "docker"
        assert kill_calls[0][1] == "kill"


def test_banned_socket_invariants_strictly_enforced(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    ws = tmp_path / "valid_ws"
    ws.mkdir()

    for pattern in BANNED_SOCKET_PATTERNS:
        # 1. Mounts
        with pytest.raises(SecurityViolationError, match="strictly prohibited"):
            runner._validate_security_invariants(
                SandboxCommandRequest(
                    command=["python", "-V"],
                    workspace_dir=ws,
                    extra_mounts=[f"/tmp/{pattern}"],
                )
            )

        # 2. Workspace
        with pytest.raises(SecurityViolationError, match="banned Docker socket"):
            runner._validate_security_invariants(
                SandboxCommandRequest(
                    command=["python", "-V"],
                    workspace_dir=f"/tmp/test/{pattern}",
                )
            )

        # 3. Command
        with pytest.raises(SecurityViolationError, match="banned Docker socket"):
            runner._validate_security_invariants(
                SandboxCommandRequest(
                    command=["cat", f"/var/{pattern}"],
                    workspace_dir=ws,
                )
            )

        # 4. Env
        with pytest.raises(SecurityViolationError, match="banned Docker socket"):
            runner._validate_security_invariants(
                SandboxCommandRequest(
                    command=["python", "-V"],
                    workspace_dir=ws,
                    env={"BAD_SOCK": pattern},
                )
            )


def test_extra_mounts_strictly_prohibited(tmp_path: Path) -> None:
    runner = SandboxRunner(base_workspace_dir=tmp_path)
    ws = tmp_path / "valid_ws"
    ws.mkdir()

    # Even innocent extra mounts must be rejected to ensure ONLY workspace is mounted
    with pytest.raises(SecurityViolationError, match="Mounting extra directories is prohibited"):
        runner._validate_security_invariants(
            SandboxCommandRequest(
                command=["python", "-V"],
                workspace_dir=ws,
                extra_mounts=["/data/shared"],
            )
        )


def test_path_boundary_enforcement(tmp_path: Path) -> None:
    base = tmp_path / "sandbox_root"
    base.mkdir()
    runner = SandboxRunner(base_workspace_dir=base)

    outside = tmp_path / "outside_dir"
    outside.mkdir()

    with pytest.raises(SecurityViolationError, match="outside permitted base directory"):
        runner._validate_security_invariants(
            SandboxCommandRequest(
                command=["python", "-V"],
                workspace_dir=outside,
            )
        )


def test_cleanup_workspace_security_guards(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    runner = SandboxRunner(base_workspace_dir=base)

    # Cannot clean up outside base
    outside = tmp_path / "outside"
    outside.mkdir()
    assert runner.cleanup_workspace(outside) is False
    assert outside.exists()

    # Cannot clean up base workspace root itself
    assert runner.cleanup_workspace(base) is False
    assert base.exists()

    # Can clean up valid subdirectory within base
    child = base / "child_ws"
    child.mkdir()
    (child / "temp.txt").write_text("hello")
    assert runner.cleanup_workspace(child) is True
    assert not child.exists()


@pytest.mark.asyncio
async def test_cleanup_workspace_async(tmp_path: Path) -> None:
    base = tmp_path / "base_async"
    base.mkdir()
    runner = SandboxRunner(base_workspace_dir=base)

    child = base / "to_delete"
    child.mkdir()
    assert await runner.cleanup_workspace_async(child) is True
    assert not child.exists()
