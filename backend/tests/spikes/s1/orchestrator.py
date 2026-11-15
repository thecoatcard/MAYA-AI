"""Safe Docker CLI orchestration for the disposable S1 benchmark environment."""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from .metrics import memory_to_bytes, parse_cpu_percent

POSTGRES_IMAGE = (
    "pgvector/pgvector:pg17@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8"
)
REDIS_IMAGE = (
    "redis:7.4-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
)
IMAGE_MEMORY_LIMIT = "768m"
IMAGE_CPU_LIMIT = "1.0"
_URL_PATTERN = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.IGNORECASE)


class CommandExecutor(Protocol):
    def run(self, args: list[str], *, timeout_seconds: float, operation: str) -> str: ...


class DockerCommandError(RuntimeError):
    """Docker failure with output scrubbed and command arguments omitted."""

    def __init__(self, operation: str, message: str, exit_code: int | None = None):
        self.operation = operation
        self.exit_code = exit_code
        safe_message = _URL_PATTERN.sub("<database-url-redacted>", message)
        super().__init__(f"{operation} failed: {safe_message[:600]}")


def redact_output(text: str) -> str:
    """Remove database URLs from untrusted tool output before surfacing errors."""
    return _URL_PATTERN.sub("<database-url-redacted>", text)


class DockerCLI:
    """Calls Docker with argv arrays, bounded timeouts, and no shell."""

    def run(self, args: list[str], *, timeout_seconds: float, operation: str) -> str:
        try:
            result = subprocess.run(
                ["docker", *args],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerCommandError(operation, "command timed out") from exc
        except OSError as exc:
            raise DockerCommandError(operation, type(exc).__name__) from exc
        if result.returncode:
            raise DockerCommandError(operation, redact_output(result.stderr), result.returncode)
        return result.stdout.strip()


@dataclass(frozen=True)
class RunOptions:
    jobs: int
    seed: int
    timeout_seconds: int
    output_path: Path

    def validate(self) -> None:
        if not 10 <= self.jobs <= 500:
            raise ValueError("jobs must be between 10 and 500")
        if not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("seed must be between 0 and 2147483647")
        if not 30 <= self.timeout_seconds <= 300:
            raise ValueError("timeout must be between 30 and 300 seconds")


@dataclass
class Sandbox:
    executor: CommandExecutor
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    owned_containers: list[str] = field(default_factory=list)
    owned_volumes: list[str] = field(default_factory=list)
    owned_networks: list[str] = field(default_factory=list)
    benchmark_image: str | None = None
    benchmark_image_id: str | None = None
    schema_initialized: bool = False
    image_ids: dict[str, str] = field(default_factory=dict)
    cleanup_errors: list[str] = field(default_factory=list)
    names: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        prefix = f"maya-s1-{self.run_id}"
        self.names = {
            "network": f"{prefix}-net",
            "postgres": f"{prefix}-pg",
            "redis": f"{prefix}-redis",
            "pg_volume": f"{prefix}-pgdata",
            "redis_volume": f"{prefix}-redisdata",
            "python_image": f"{prefix}:runner",
            "celery_driver": f"{prefix}-celery",
            "arq_driver": f"{prefix}-arq",
        }

    def _inspect(self, kind: str, name: str) -> bool:
        try:
            self.executor.run(
                [kind, "inspect", name],
                timeout_seconds=10,
                operation=f"inspect-{kind}",
            )
            return True
        except DockerCommandError as exc:
            if exc.exit_code == 1:
                return False
            raise

    def _assert_name_free(self, kind: str, name: str) -> None:
        if self._inspect(kind, name):
            raise DockerCommandError(
                "collision-check", f"generated Docker {kind} name already exists: {name}"
            )

    def _create_volume(self, name: str) -> None:
        self._assert_name_free("volume", name)
        self.executor.run(
            [
                "volume",
                "create",
                "--label",
                f"maya.s1.run_id={self.run_id}",
                name,
            ],
            timeout_seconds=20,
            operation="create-volume",
        )
        if self._resource_run_id("volume", name) != self.run_id:
            raise DockerCommandError("create-volume", "created volume ownership did not verify")
        self.owned_volumes.append(name)

    def _create_network(self) -> None:
        name = self.names["network"]
        self._assert_name_free("network", name)
        self.executor.run(
            [
                "network",
                "create",
                "--internal",
                "--label",
                f"maya.s1.run_id={self.run_id}",
                name,
            ],
            timeout_seconds=20,
            operation="create-network",
        )
        if self._resource_run_id("network", name) != self.run_id:
            raise DockerCommandError("create-network", "created network ownership did not verify")
        self.owned_networks.append(name)

    def _resource_run_id(self, kind: str, name: str) -> str:
        template = (
            '{{index .Config.Labels "maya.s1.run_id"}}'
            if kind == "container"
            else '{{index .Labels "maya.s1.run_id"}}'
        )
        return self.executor.run(
            [kind, "inspect", "--format", template, name],
            timeout_seconds=10,
            operation=f"verify-{kind}-ownership",
        )

    def _image_id(self, image: str) -> str:
        value = self.executor.run(
            ["image", "inspect", "--format", "{{.Id}}", image],
            timeout_seconds=20,
            operation="inspect-image",
        )
        if not value.startswith("sha256:"):
            raise DockerCommandError("inspect-image", "Docker returned an invalid image id")
        return value

    def _ensure_external_image(self, image: str) -> str:
        try:
            image_id = self._image_id(image)
        except DockerCommandError as exc:
            if exc.exit_code != 1:
                raise
            self.executor.run(
                ["pull", image],
                timeout_seconds=120,
                operation="pull-image",
            )
            image_id = self._image_id(image)
        self.image_ids[image] = image_id
        return image_id

    def _build_benchmark_image(self, context: Path, candidate: str) -> None:
        tag = self.names["python_image"]
        self._assert_name_free("image", tag)
        dockerfile = (
            context / "celery_compat" / "Dockerfile"
            if candidate == "celery-compat"
            else context / "Dockerfile"
        )
        self.executor.run(
            [
                "build",
                "--file",
                str(dockerfile),
                "--label",
                f"maya.s1.run_id={self.run_id}",
                "--tag",
                tag,
                str(context),
            ],
            timeout_seconds=180,
            operation="build-benchmark-image",
        )
        self.benchmark_image = tag
        self.benchmark_image_id = self._image_id(tag)
        if self._image_run_id(tag) != self.run_id:
            raise DockerCommandError("build-benchmark-image", "image ownership did not verify")
        self.image_ids["benchmark"] = self.benchmark_image_id

    def _image_run_id(self, image: str) -> str:
        return self.executor.run(
            [
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "maya.s1.run_id"}}',
                image,
            ],
            timeout_seconds=20,
            operation="verify-image-ownership",
        )

    def _start_container(self, args: list[str], operation: str) -> str:
        try:
            name_index = args.index("--name") + 1
            name = args[name_index]
        except (ValueError, IndexError) as exc:
            raise ValueError("owned containers require an explicit name") from exc
        self._assert_name_free("container", name)
        container_id = self.executor.run(
            ["run", "--detach", *args],
            timeout_seconds=30,
            operation=operation,
        )
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise DockerCommandError(operation, "Docker returned an invalid container id")
        if self._resource_run_id("container", container_id) != self.run_id:
            raise DockerCommandError(operation, "created container ownership did not verify")
        self.owned_containers.append(container_id)
        return container_id

    def _start_services(self) -> dict[str, str]:
        postgres_image_id = self._ensure_external_image(POSTGRES_IMAGE)
        redis_image_id = self._ensure_external_image(REDIS_IMAGE)
        pg_volume = self.names["pg_volume"]
        redis_volume = self.names["redis_volume"]
        self._create_volume(pg_volume)
        self._create_volume(redis_volume)
        self._create_network()
        pg_id = self._start_container(
            [
                "--name",
                self.names["postgres"],
                "--label",
                f"maya.s1.run_id={self.run_id}",
                "--network",
                self.names["network"],
                "--network-alias",
                "postgres",
                "--cpus",
                "0.5",
                "--memory",
                "512m",
                "--mount",
                f"source={pg_volume},target=/var/lib/postgresql/data",
                "--env",
                "POSTGRES_DB=s1_benchmark",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                postgres_image_id,
            ],
            "start-postgres",
        )
        redis_id = self._start_container(
            [
                "--name",
                self.names["redis"],
                "--label",
                f"maya.s1.run_id={self.run_id}",
                "--network",
                self.names["network"],
                "--network-alias",
                "redis",
                "--cpus",
                "0.25",
                "--memory",
                "256m",
                "--mount",
                f"source={redis_volume},target=/data",
                redis_image_id,
                "redis-server",
                "--appendonly",
                "yes",
                "--appendfsync",
                "everysec",
                "--save",
                "",
            ],
            "start-redis",
        )
        self._wait_service(pg_id, ["pg_isready", "-U", "postgres", "-d", "s1_benchmark"])
        self._wait_service(redis_id, ["redis-cli", "ping"])
        return {"postgres": pg_id, "redis": redis_id}

    def _wait_service(
        self, container_id: str, command: list[str], timeout_seconds: float = 40
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                output = self.executor.run(
                    ["exec", container_id, *command],
                    timeout_seconds=5,
                    operation="service-readiness",
                )
                if "accepting connections" in output or output == "PONG":
                    return
            except DockerCommandError:
                pass
            time.sleep(0.25)
        raise DockerCommandError("service-readiness", "isolated service readiness timed out")

    def _one_shot(self, command: list[str], timeout_seconds: int = 30) -> str:
        if self.benchmark_image is None:
            raise RuntimeError("benchmark image has not been built")
        return self.executor.run(
            [
                "run",
                "--rm",
                "--network",
                self.names["network"],
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--env",
                "S1_DATABASE_URL=postgresql://postgres@postgres:5432/s1_benchmark",
                "--env",
                "S1_REDIS_URL=redis://redis:6379/0",
                "--env",
                f"S1_RUN_ID={self.run_id}",
                "--env",
                "S1_BACKEND=bootstrap",
                self.benchmark_image,
                *command,
            ],
            timeout_seconds=timeout_seconds,
            operation="run-bootstrap-container",
        )

    def _run_driver(
        self, backend: str, jobs: int, seed: int, timeout_seconds: int
    ) -> dict[str, Any]:
        if self.benchmark_image is None:
            raise RuntimeError("benchmark image has not been built")
        name = self.names[f"{backend}_driver"]
        self._assert_name_free("container", name)
        container_id = self._start_container(
            [
                "--name",
                name,
                "--label",
                f"maya.s1.run_id={self.run_id}",
                "--network",
                self.names["network"],
                "--cpus",
                IMAGE_CPU_LIMIT,
                "--memory",
                IMAGE_MEMORY_LIMIT,
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--env",
                "S1_DATABASE_URL=postgresql://postgres@postgres:5432/s1_benchmark",
                "--env",
                "S1_REDIS_URL=redis://redis:6379/0",
                "--env",
                f"S1_RUN_ID={self.run_id}",
                "--env",
                f"S1_BACKEND={backend}",
                "--env",
                "S1_CONCURRENCY=2",
                "--env",
                "S1_ARQ_POLL_DELAY=0.02",
                self.benchmark_image,
                "run",
                "--jobs",
                str(jobs),
                "--seed",
                str(seed),
            ],
            f"run-{backend}-driver",
        )
        samples: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout_seconds
        started = time.monotonic()
        while time.monotonic() < deadline:
            status = self.executor.run(
                ["inspect", "--format", "{{.State.Running}}|{{.State.ExitCode}}", container_id],
                timeout_seconds=10,
                operation="inspect-driver-status",
            )
            running, exit_code_text = status.split("|", maxsplit=1)
            if running.lower() != "true":
                break
            samples.append(self._sample_stats(container_id, time.monotonic() - started))
            time.sleep(0.2)
        else:
            raise DockerCommandError(f"{backend}-driver", "benchmark container timed out")
        exit_code = int(exit_code_text)
        logs = self.executor.run(
            ["logs", container_id],
            timeout_seconds=20,
            operation=f"read-{backend}-driver-output",
        )
        if exit_code:
            raise DockerCommandError(f"{backend}-driver", redact_output(logs[-2000:]), exit_code)
        try:
            payload = cast(dict[str, Any], json.loads(logs.splitlines()[-1]))
        except (IndexError, json.JSONDecodeError) as exc:
            raise DockerCommandError(
                f"{backend}-driver", "driver did not return valid JSON output"
            ) from exc
        payload["container_stats"] = {
            "sample_count": len(samples),
            "samples": samples,
            "maximum_memory_bytes": max((row["memory_bytes"] for row in samples), default=0),
            "maximum_cpu_percent": max((row["cpu_percent"] for row in samples), default=0),
            "maximum_process_count": max((row["process_count"] for row in samples), default=0),
            "memory_limit": IMAGE_MEMORY_LIMIT,
            "cpu_limit": IMAGE_CPU_LIMIT,
        }
        payload["container_id"] = container_id
        return payload

    def _sample_stats(self, container_id: str, monotonic_time: float) -> dict[str, Any]:
        raw = self.executor.run(
            [
                "stats",
                "--no-stream",
                "--format",
                "{{.MemUsage}}|{{.CPUPerc}}|{{.PIDs}}",
                container_id,
            ],
            timeout_seconds=10,
            operation="sample-container-stats",
        )
        try:
            match = re.fullmatch(r"([^|]+?)\s*/\s*([^|]+)\|([^|]+)\|([0-9]+)", raw)
            if match is None:
                raise ValueError("malformed stats output")
            memory, _limit, cpu, pids = match.groups()
            memory_bytes = memory_to_bytes(memory)
            cpu_percent = parse_cpu_percent(cpu)
            process_count = int(pids)
        except (AttributeError, ValueError) as exc:
            raise DockerCommandError(
                "sample-container-stats", "Docker returned malformed stats"
            ) from exc
        return {
            "elapsed_monotonic_s": monotonic_time,
            "memory_bytes": memory_bytes,
            "cpu_percent": cpu_percent,
            "process_count": process_count,
            "raw": raw,
        }

    def execute(
        self,
        context: Path,
        jobs: int,
        seed: int,
        timeout_seconds: int,
        candidate: str = "comparison",
    ) -> dict[str, Any]:
        docker_version = self.executor.run(
            ["version", "--format", "{{.Client.Version}}|{{.Server.Version}}"],
            timeout_seconds=10,
            operation="check-docker",
        )
        image_ids = self._start_services()
        self._build_benchmark_image(context, candidate)
        self.schema_initialized = True
        self._one_shot(["init-db"])
        celery = self._run_driver("celery", jobs, seed, timeout_seconds)
        if candidate == "celery-compat":
            return {
                "run_id": self.run_id,
                "docker_version": docker_version,
                "candidate": candidate,
                "configuration": {
                    "isolated_internal_network": True,
                    "published_ports": [],
                    "host_mounts": [],
                    "worker_driver_cpus": float(IMAGE_CPU_LIMIT),
                    "worker_driver_memory": IMAGE_MEMORY_LIMIT,
                    "postgres_database": "s1_benchmark",
                    "postgres_authentication": "trust on isolated private network only",
                    "redis_server": "7.4 with AOF enabled",
                    "worker_concurrency": 2,
                    "celery_redis_visibility_timeout_seconds": 3,
                    "candidate_lock": "celery_compat/uv.lock",
                },
                "docker_images": {
                    **self.image_ids,
                    "benchmark_image_id": self.benchmark_image_id,
                    "postgres_container_id": image_ids["postgres"],
                    "redis_container_id": image_ids["redis"],
                },
                "celery_compat": celery,
            }
        arq = self._run_driver("arq", jobs, seed, timeout_seconds)
        return {
            "run_id": self.run_id,
            "docker_version": docker_version,
            "configuration": {
                "isolated_internal_network": True,
                "published_ports": [],
                "host_mounts": [],
                "python_base_image": (
                    "python:3.12.10-slim-bookworm@sha256:"
                    "fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db"
                ),
                "postgres_database": "s1_benchmark",
                "postgres_authentication": "trust on isolated private network only",
                "redis": ["7.4", "AOF enabled", "appendfsync everysec"],
                "worker_driver_cpus": float(IMAGE_CPU_LIMIT),
                "worker_driver_memory": IMAGE_MEMORY_LIMIT,
                "worker_concurrency": 2,
                "arq_poll_delay_seconds": 0.02,
                "arq_job_timeout_seconds": 12,
                "celery_redis_visibility_timeout_seconds": 3,
            },
            "docker_images": {
                **self.image_ids,
                "benchmark_image_id": self.benchmark_image_id,
                "postgres_container_id": image_ids["postgres"],
                "redis_container_id": image_ids["redis"],
            },
            "celery": celery,
            "arq": arq,
        }

    def cleanup(self) -> dict[str, Any]:
        removed: dict[str, list[str]] = {
            "containers": [],
            "volumes": [],
            "networks": [],
            "images": [],
        }
        schema_dropped = False
        if self.schema_initialized and self.benchmark_image is not None:
            try:
                self._one_shot(["drop-db"], timeout_seconds=30)
                schema_dropped = True
            except Exception as exc:
                self.cleanup_errors.append(f"drop-synthetic-schema failed: {type(exc).__name__}")
        for container_id in reversed(self.owned_containers):
            self._cleanup_one(
                ["rm", "--force", container_id],
                "remove-owned-container",
                removed["containers"],
                container_id,
            )
        for volume in reversed(self.owned_volumes):
            self._cleanup_labeled_resource(
                "volume", volume, ["volume", "rm", volume], removed["volumes"]
            )
        for network in reversed(self.owned_networks):
            self._cleanup_labeled_resource(
                "network", network, ["network", "rm", network], removed["networks"]
            )
        if self.benchmark_image and self.benchmark_image_id:
            try:
                current = self._image_id(self.benchmark_image)
                owner = self._image_run_id(self.benchmark_image)
                if current == self.benchmark_image_id and owner == self.run_id:
                    self._cleanup_one(
                        ["image", "rm", self.benchmark_image],
                        "remove-owned-image-tag",
                        removed["images"],
                        self.benchmark_image,
                    )
                else:
                    self.cleanup_errors.append("benchmark image tag changed; left untouched")
            except DockerCommandError as exc:
                if exc.exit_code != 1:
                    self.cleanup_errors.append(str(exc))
        return {
            "synthetic_schema_dropped": schema_dropped,
            "removed": removed,
            "errors": list(self.cleanup_errors),
        }

    def _cleanup_labeled_resource(
        self, kind: str, name: str, args: list[str], removed: list[str]
    ) -> None:
        try:
            owner = self._resource_run_id(kind, name)
        except DockerCommandError as exc:
            self.cleanup_errors.append(f"inspect {kind} ownership failed: {exc}")
            return
        if owner != self.run_id:
            self.cleanup_errors.append(f"{kind} ownership changed; resource left untouched")
            return
        self._cleanup_one(args, f"remove-owned-{kind}", removed, name)

    def _cleanup_one(self, args: list[str], operation: str, removed: list[str], label: str) -> None:
        try:
            self.executor.run(args, timeout_seconds=20, operation=operation)
            removed.append(label)
        except DockerCommandError as exc:
            self.cleanup_errors.append(str(exc))


def run_sandbox(
    context: Path,
    jobs: int,
    seed: int,
    timeout_seconds: int,
    executor: CommandExecutor | None = None,
    candidate: str = "comparison",
) -> dict[str, Any]:
    if candidate not in {"comparison", "celery-compat"}:
        raise ValueError("candidate must be comparison or celery-compat")
    sandbox = Sandbox(executor or DockerCLI())
    data: dict[str, Any] = {
        "status": "failed",
        "run_id": sandbox.run_id,
        "config": {
            "jobs": jobs,
            "seed": seed,
            "timeout_seconds_per_backend": timeout_seconds,
            "candidate": candidate,
            "concurrency": 2,
            "workload": "three async LangGraph nodes; synthetic I/O delays; PostgreSQL receipts",
        },
        "results": {},
        "error": None,
    }
    try:
        data["results"] = sandbox.execute(context, jobs, seed, timeout_seconds, candidate=candidate)
        data["status"] = "completed"
    except Exception as exc:
        data["error"] = {
            "type": type(exc).__name__,
            "message": redact_output(str(exc))[:800],
        }
    finally:
        data["cleanup"] = sandbox.cleanup()
        if data["cleanup"]["errors"]:
            data["status"] = "cleanup_failed"
    return data
