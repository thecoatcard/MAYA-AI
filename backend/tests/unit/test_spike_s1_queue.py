from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.spikes import spike_s1_queue as spike
from tests.spikes.s1.metrics import (
    classify_cancellation_status,
    memory_to_bytes,
    parse_cpu_percent,
    percentile,
    summarize,
)
from tests.spikes.s1.orchestrator import (
    DockerCLI,
    DockerCommandError,
    RunOptions,
    Sandbox,
    redact_output,
    run_sandbox,
)


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_operations: set[str] = set()
        self.inspect_existing: set[str] = set()

    def run(self, args: list[str], *, timeout_seconds: float, operation: str) -> str:
        del timeout_seconds
        self.calls.append(args)
        if operation in self.fail_operations:
            raise DockerCommandError(operation, "synthetic failure")
        if args[:2] in (
            ["container", "inspect"],
            ["volume", "inspect"],
            ["network", "inspect"],
        ):
            if "--format" in args:
                return "test"
            if args[-1] in self.inspect_existing:
                return "existing"
            raise DockerCommandError(operation, "not found", 1)
        if args[:3] == ["image", "inspect", "--format"]:
            if "maya.s1.run_id" in args[3]:
                return "test"
            return "sha256:" + "a" * 64
        if args[:2] == ["image", "inspect"]:
            if args[-1] in self.inspect_existing:
                return "existing"
            raise DockerCommandError(operation, "not found", 1)
        if args[:3] == ["image", "rm", "maya-s1-test:runner"]:
            return "maya-s1-test:runner"
        return "fake-result"


def test_host_helpers_use_canonical_package_names() -> None:
    assert spike.__name__ == "tests.spikes.spike_s1_queue"
    assert RunOptions.__module__ == "tests.spikes.s1.orchestrator"
    assert summarize.__module__ == "tests.spikes.s1.metrics"


def test_direct_cli_plan_works_without_site_packages(tmp_path: Path) -> None:
    # -I -S removes local/site packages and PYTHONPATH: host-side planning must
    # not import ARQ, Celery, LangGraph, asyncpg, or the container worker modules.
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(Path(spike.__file__).resolve()), "--jobs", "10"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(completed.stdout)["status"] == "plan_only"


def test_default_cli_prints_plan_without_starting_docker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_if_run(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("plan-only mode must not run Docker")

    monkeypatch.setattr(spike, "run_sandbox", fail_if_run)
    assert spike.run(["--jobs", "20", "--seed", "7"]) == 0
    output = capsys.readouterr().out
    assert '"status": "plan_only"' in output
    assert '"arq_0.28.0": "redis-py>=4.2.0,<6"' in output


def test_compatibility_cli_plan_is_candidate_specific_and_docker_free(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        spike,
        "run_sandbox",
        lambda *args, **kwargs: pytest.fail("plan-only mode must not run Docker"),
    )
    assert spike.run(["--candidate", "celery-compat", "--jobs", "10"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["candidate"] == "celery-compat"
    assert plan["lock_file"].endswith("s1/celery_compat/uv.lock")
    assert plan["dependencies"]["redis-py"] == "8.1.0"
    assert plan["backend_order"] == ["Celery 5.6.3"]


def test_compatibility_candidate_is_passed_to_runner_and_lock_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "candidate.json"
    seen: dict[str, Any] = {}

    def fake_run_sandbox(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "completed", "run_id": "candidate-run", "cleanup": {}}

    monkeypatch.setattr(spike, "run_sandbox", fake_run_sandbox)
    assert (
        spike.run(
            ["--run", "--candidate", "celery-compat", "--jobs", "10", "--output", str(output)]
        )
        == 0
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert seen["candidate"] == "celery-compat"
    assert result["invocation"]["candidate"] == "celery-compat"
    assert result["invocation"]["lock_sha256"] == spike._compat_lock_sha256()
    assert "celery_compat/uv.lock" in result["invocation"]["lock_file"]


def test_unknown_candidate_is_rejected_before_docker() -> None:
    with pytest.raises(ValueError, match="candidate"):
        run_sandbox(Path("unused"), jobs=10, seed=5, timeout_seconds=30, candidate="unknown")


def test_cancellation_classification_requires_terminal_abort() -> None:
    assert classify_cancellation_status("aborted") == "terminal_aborted"
    assert classify_cancellation_status("completed") == "terminal_non_cancel_state"
    assert classify_cancellation_status("failed") == "terminal_non_cancel_state"
    assert classify_cancellation_status("running") == "request_only_no_terminal_state_observed"
    assert classify_cancellation_status(None) == "request_only_no_terminal_state_observed"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--run", "--jobs", "9"], "jobs must be between"),
        (["--run", "--jobs", "501"], "jobs must be between"),
        (["--run", "--seed", "-1"], "seed must be between"),
        (["--run", "--timeout-seconds", "29"], "timeout must be between"),
        (["--jobs", "501"], "jobs must be between"),
    ],
)
def test_invalid_cli_values_are_rejected_before_docker(
    arguments: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spike,
        "run_sandbox",
        lambda *args, **kwargs: pytest.fail("Docker must not run for invalid input"),
    )
    with pytest.raises(ValueError, match=expected):
        spike.run(arguments)


def test_existing_output_path_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "existing.json"
    output.write_text('{"keep": true}\n', encoding="utf-8")
    monkeypatch.setattr(
        spike,
        "run_sandbox",
        lambda *args, **kwargs: pytest.fail("existing output must be rejected first"),
    )
    with pytest.raises(FileExistsError, match="already exists"):
        spike.run(["--run", "--output", str(output)])
    assert output.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_run_options_accept_only_bounded_measurement_parameters() -> None:
    RunOptions(10, 0, 30, Path("unused")).validate()
    RunOptions(500, 2**31 - 1, 300, Path("unused")).validate()
    with pytest.raises(ValueError):
        RunOptions(0, 1, 30, Path("unused")).validate()
    with pytest.raises(ValueError):
        RunOptions(10, 1, 301, Path("unused")).validate()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0B", 0),
        ("1.5MiB", 1_572_864),
        ("2GB", 2_000_000_000),
        ("1.25GiB", 1_342_177_280),
    ],
)
def test_docker_memory_parser_handles_reported_units(raw: str, expected: int) -> None:
    assert memory_to_bytes(raw) == expected


@pytest.mark.parametrize("raw", ["", "-1MiB", "1.5", "1ZiB", "1MiB/2MiB"])
def test_docker_memory_parser_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError):
        memory_to_bytes(raw)


def test_percentiles_are_nearest_rank_and_empty_input_is_explicit() -> None:
    assert percentile([], 0.95) is None
    assert percentile([4, 1, 3, 2], 0.50) == 2
    assert summarize([1, 2, 3, 4]) == {"count": 4, "p50": 2.0, "p95": 4.0, "max": 4}


@pytest.mark.parametrize("quantile", [-0.01, 1.01])
def test_percentile_rejects_out_of_range_quantile(quantile: float) -> None:
    with pytest.raises(ValueError, match="quantile"):
        percentile([1, 2], quantile)


def test_percentile_rejects_non_finite_measurements() -> None:
    with pytest.raises(ValueError, match="finite"):
        percentile([1.0, float("nan")], 0.95)


@pytest.mark.parametrize(("raw", "expected"), [("0.25%", 0.25), ("120%", 120.0)])
def test_cpu_percentage_parser(raw: str, expected: float) -> None:
    assert parse_cpu_percent(raw) == expected


@pytest.mark.parametrize("raw", ["10", "-1%", "nan%", "inf%", "bad%"])
def test_cpu_percentage_parser_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cpu_percent(raw)


def test_docker_command_uses_argv_and_never_a_shell() -> None:
    completed = subprocess.CompletedProcess(
        args=["docker", "version"], returncode=0, stdout="ok\n", stderr=""
    )
    with patch(
        "tests.spikes.s1.orchestrator.subprocess.run", return_value=completed
    ) as run_process:
        assert DockerCLI().run(["version"], timeout_seconds=4, operation="check-docker") == "ok"
    assert run_process.call_args.args[0] == ["docker", "version"]
    assert run_process.call_args.kwargs["shell"] is False
    assert run_process.call_args.kwargs["timeout"] == 4


def test_docker_errors_and_output_redact_database_urls() -> None:
    scheme = "postgre" + "sql"
    password = "priv" + "ate"
    database_url = scheme + "://" + "synthetic" + ":" + password + "@db:5432/test"
    error = DockerCommandError(
        "driver",
        f"failed {database_url}",
        exit_code=1,
    )
    assert database_url not in str(error)
    assert redact_output(database_url) == "<database-url-redacted>"


def test_run_sandbox_reports_error_and_cleans_up_without_exposing_urls() -> None:
    scheme = "postgre" + "sql"
    password = "priv" + "ate"
    database_url = scheme + "://" + "local" + ":" + password + "@postgres/db"

    class FailedExecutor(FakeExecutor):
        def run(self, args: list[str], *, timeout_seconds: float, operation: str) -> str:
            del args, timeout_seconds
            raise DockerCommandError(operation, f"could not connect to {database_url}", 1)

    result = run_sandbox(
        Path("unused"), jobs=10, seed=5, timeout_seconds=30, executor=FailedExecutor()
    )
    assert result["status"] == "failed"
    assert result["cleanup"]["errors"] == []
    assert database_url not in str(result)


def test_cleanup_targets_only_owned_resources() -> None:
    executor = FakeExecutor()
    sandbox = Sandbox(executor, run_id="test")
    sandbox.owned_containers.append("own-container-id")
    sandbox.owned_volumes.append("maya-s1-test-pgdata")
    sandbox.owned_networks.append("maya-s1-test-net")
    sandbox.benchmark_image = "maya-s1-test:runner"
    sandbox.benchmark_image_id = "sha256:" + "a" * 64

    result = sandbox.cleanup()
    commands = executor.calls
    assert ["rm", "--force", "own-container-id"] in commands
    assert ["volume", "rm", "maya-s1-test-pgdata"] in commands
    assert ["network", "rm", "maya-s1-test-net"] in commands
    assert ["image", "rm", "maya-s1-test:runner"] in commands
    assert all("existing-container" not in " ".join(command) for command in commands)
    assert result["errors"] == []


def test_collision_check_fails_closed_before_creating_resource() -> None:
    executor = FakeExecutor()
    executor.inspect_existing.add("maya-s1-test-net")
    sandbox = Sandbox(executor, run_id="test")
    with pytest.raises(DockerCommandError, match="already exists"):
        sandbox._create_network()
    assert not any(command[:2] == ["network", "create"] for command in executor.calls)


def test_candidate_dockerfile_is_selected_for_image_build(tmp_path: Path) -> None:
    executor = FakeExecutor()
    sandbox = Sandbox(executor, run_id="test")
    sandbox._build_benchmark_image(tmp_path, "celery-compat")
    build = next(command for command in executor.calls if command[0] == "build")
    assert str(tmp_path / "celery_compat" / "Dockerfile") == build[build.index("--file") + 1]


def test_candidate_image_collision_fails_closed_before_build() -> None:
    executor = FakeExecutor()
    executor.inspect_existing.add("maya-s1-candidate:runner")
    sandbox = Sandbox(executor, run_id="candidate")
    with pytest.raises(DockerCommandError, match="already exists"):
        sandbox._build_benchmark_image(Path("unused"), "celery-compat")
    assert not any(command[0] == "build" for command in executor.calls)


def test_stats_parser_records_memory_cpu_and_pid_count() -> None:
    executor = FakeExecutor()
    executor.run = lambda args, **kwargs: "2.0MiB / 768MiB|12.50%|7"  # type: ignore[method-assign]
    sandbox = Sandbox(executor, run_id="test")
    sample = sandbox._sample_stats("own-container", 0.25)
    assert sample["memory_bytes"] == 2_097_152
    assert sample["cpu_percent"] == 12.5
    assert sample["process_count"] == 7
    assert sample["elapsed_monotonic_s"] == 0.25


def test_stats_parser_rejects_malformed_output() -> None:
    executor = FakeExecutor()
    executor.run = lambda args, **kwargs: "not docker stats"  # type: ignore[method-assign]
    sandbox = Sandbox(executor, run_id="test")
    with pytest.raises(DockerCommandError, match="malformed stats"):
        sandbox._sample_stats("own-container", 0.25)
