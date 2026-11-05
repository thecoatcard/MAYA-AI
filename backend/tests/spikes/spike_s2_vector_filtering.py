"""Opt-in synthetic pgvector benchmark for workspace/user-filtered HNSW queries."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import re
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg

DIMENSIONS = 768
SEED = 20_261_105
TOP_K = 10
WORKSPACE_SIZES = (3_000, 6_000, 9_000, 12_000)
USER_WEIGHTS = (1, 1, 2, 2, 3, 3, 4, 8)
QUERY_USER_INDICES = (0, 7)
ITERATIVE_SCAN_MODES = ("off", "strict_order", "relaxed_order")
EF_SEARCH = 40
MAX_SCAN_TUPLES = 20_000
SCAN_MEM_MULTIPLIER = 1
REPETITIONS = 5
WARMUP_RUNS = 1
CONTAINER_MEMORY = "3g"
CONTAINER_CPUS = "2"
CONTAINER_START_TIMEOUT_SECONDS = 90
DOCKER_OPERATION_TIMEOUT_SECONDS = 30
DATABASE_OPERATION_TIMEOUT_SECONDS = 120
BENCHMARK_TIMEOUT_SECONDS = 1_800
IMAGE = (
    "pgvector/pgvector:pg17@"
    + "sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8"
)

ANN_QUERY = """
/* S2 ANN path; keep distinct from the exact baseline statement. */
SELECT id, workspace_id, user_id, embedding <=> $3::vector AS distance
FROM spike_s2_items
WHERE workspace_id = $1 AND user_id = $2
ORDER BY embedding <=> $3::vector
LIMIT $4
"""
EXACT_QUERY = """
SELECT id, workspace_id, user_id, embedding <=> $3::vector AS distance
FROM spike_s2_items
WHERE workspace_id = $1 AND user_id = $2
ORDER BY embedding <=> $3::vector
LIMIT $4
"""


@dataclass(frozen=True)
class WorkloadCase:
    workspace_id: int
    user_id: int
    workspace_rows: int
    matching_rows: int
    total_rows: int

    @property
    def workspace_selectivity(self) -> float:
        return self.workspace_rows / self.total_rows

    @property
    def user_selectivity_within_workspace(self) -> float:
        return self.matching_rows / self.workspace_rows

    @property
    def combined_selectivity(self) -> float:
        return self.matching_rows / self.total_rows


def random_unit_vector(rng: random.Random, dimensions: int = DIMENSIONS) -> tuple[float, ...]:
    """Return a seeded, finite, unit-length Gaussian vector."""
    if dimensions < 1:
        raise ValueError("dimensions must be positive")
    values = tuple(rng.gauss(0.0, 1.0) for _ in range(dimensions))
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("generated vector has an invalid norm")
    return tuple(value / norm for value in values)


def vector_literal(vector: Sequence[float]) -> str:
    """Serialize a vector in pgvector's text input format."""
    if not vector:
        raise ValueError("vector must not be empty")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("vector values must be finite")
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def recall_at_k(expected_ids: Sequence[int], actual_ids: Sequence[int], k: int) -> float:
    """Compute set-based recall against the exact top-k result, without duplicate inflation."""
    if k < 1:
        raise ValueError("k must be positive")
    expected = set(expected_ids[:k])
    actual = set(actual_ids[:k])
    if not expected:
        return 1.0 if not actual else 0.0
    return len(expected & actual) / len(expected)


def assert_filter_confinement(
    rows: Sequence[dict[str, Any]], workspace_id: int, user_id: int
) -> None:
    """Reject any result that escaped either tenant predicate."""
    if any(row["workspace_id"] != workspace_id or row["user_id"] != user_id for row in rows):
        raise AssertionError("query returned a row outside its workspace/user predicates")


def percentile(values: Sequence[float], percentile_value: float) -> float:
    """Return an interpolated percentile in [0, 100] for a non-empty sample."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def workload_cases() -> list[WorkloadCase]:
    cases: list[WorkloadCase] = []
    total_rows = sum(WORKSPACE_SIZES)
    for workspace_id, workspace_rows in enumerate(WORKSPACE_SIZES, start=1):
        counts = [workspace_rows * weight // sum(USER_WEIGHTS) for weight in USER_WEIGHTS]
        counts[-1] += workspace_rows - sum(counts)
        for user_index in QUERY_USER_INDICES:
            cases.append(
                WorkloadCase(
                    workspace_id=workspace_id,
                    user_id=workspace_id * 100 + user_index,
                    workspace_rows=workspace_rows,
                    matching_rows=counts[user_index],
                    total_rows=total_rows,
                )
            )
    return cases


def _sample_percentiles(samples_ms: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(samples_ms, 50), 4),
        "p95_ms": round(percentile(samples_ms, 95), 4),
        "min_ms": round(min(samples_ms), 4),
        "max_ms": round(max(samples_ms), 4),
    }


def _docker(
    arguments: Sequence[str], *, label: str, timeout: int = DOCKER_OPERATION_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run a bounded Docker CLI command without ever echoing command arguments."""
    try:
        result = subprocess.run(
            ["docker", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Docker operation failed: {label}") from error
    if result.returncode:
        raise RuntimeError(f"Docker operation failed: {label} (exit {result.returncode})")
    return result


class TemporaryPostgres:
    """Owns only one uniquely named temporary container and one temporary volume."""

    def __init__(self) -> None:
        token = uuid4().hex[:12]
        self.container_name = f"maya-s2-{token}"
        self.volume_name = f"maya-s2-data-{token}"
        self.password = secrets.token_urlsafe(32)
        self.volume_created = False
        self.container_created = False
        self.host_port: int | None = None

    @staticmethod
    def _ensure_name_unused(kind: str, name: str) -> None:
        if kind == "volume":
            result = _docker(
                ["volume", "ls", "--quiet", "--filter", f"name=^{name}$"],
                label="check temporary volume name",
            )
        elif kind == "container":
            result = _docker(
                ["ps", "--all", "--quiet", "--filter", f"name=^/{name}$"],
                label="check temporary container name",
            )
        else:
            raise ValueError(f"unsupported Docker resource kind: {kind}")
        if result.returncode == 0:
            if result.stdout.strip():
                raise RuntimeError(f"refusing to reuse existing Docker {kind}: {name}")

    def start(self) -> None:
        image = _docker(["image", "inspect", IMAGE], label="verify pinned image")
        if not image.stdout.strip():
            raise RuntimeError("pinned benchmark image is unavailable locally")
        self._ensure_name_unused("volume", self.volume_name)
        self._ensure_name_unused("container", self.container_name)
        _docker(["volume", "create", self.volume_name], label="create temporary volume")
        self.volume_created = True
        created = _docker(
            [
                "create",
                "--name",
                self.container_name,
                "--label",
                "maya.synthetic-spike=s2",
                "--memory",
                CONTAINER_MEMORY,
                "--memory-swap",
                CONTAINER_MEMORY,
                "--cpus",
                CONTAINER_CPUS,
                "--publish",
                "127.0.0.1::5432",
                "--mount",
                f"type=volume,source={self.volume_name},target=/var/lib/postgresql/data",
                "--env",
                "POSTGRES_DB=spike_s2",
                "--env",
                "POSTGRES_USER=spike_s2",
                "--env",
                f"POSTGRES_PASSWORD={self.password}",
                "--pull=never",
                IMAGE,
            ],
            label="create temporary PostgreSQL container",
        )
        if not created.stdout.strip():
            raise RuntimeError("Docker did not return the temporary container id")
        self.container_created = True
        _docker(["start", self.container_name], label="start temporary PostgreSQL container")
        port_result = _docker(
            ["port", self.container_name, "5432/tcp"],
            label="read temporary PostgreSQL loopback port",
        )
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)\s*", port_result.stdout.strip())
        if match is None:
            raise RuntimeError("temporary PostgreSQL port was not bound to loopback")
        self.host_port = int(match.group(1))

    async def connect(self) -> asyncpg.Connection:
        if self.host_port is None:
            raise RuntimeError("temporary PostgreSQL container is not started")
        deadline = time.monotonic() + CONTAINER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                return await asyncio.wait_for(
                    asyncpg.connect(
                        host="127.0.0.1",
                        port=self.host_port,
                        user="spike_s2",
                        password=self.password,
                        database="spike_s2",
                        statement_cache_size=0,
                        timeout=5,
                    ),
                    timeout=6,
                )
            except (OSError, asyncpg.PostgresError, TimeoutError):
                await asyncio.sleep(1)
        raise RuntimeError("temporary PostgreSQL did not become ready before timeout")

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        if self.container_created:
            try:
                _docker(
                    ["rm", "--force", "--volumes", self.container_name],
                    label="remove owned temporary container",
                )
            except RuntimeError:
                failures.append("temporary container removal failed")
        if self.volume_created:
            try:
                _docker(["volume", "rm", self.volume_name], label="remove owned temporary volume")
            except RuntimeError:
                failures.append("temporary volume removal failed")
        return failures


def _parse_explain(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        raise RuntimeError("PostgreSQL returned an unexpected EXPLAIN JSON shape")
    return value[0]


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node)
        for child in node.get("Plans", []):
            visit(child)

    visit(plan["Plan"])
    return nodes


def _hnsw_index_node(plan: dict[str, Any]) -> dict[str, Any]:
    matches = [
        node
        for node in _plan_nodes(plan)
        if node.get("Index Name") == "spike_s2_items_embedding_hnsw"
    ]
    if len(matches) != 1:
        raise RuntimeError("EXPLAIN must contain exactly one HNSW index scan")
    return matches[0]


def _validate_hnsw_filter(plan: dict[str, Any]) -> dict[str, Any]:
    node = _hnsw_index_node(plan)
    filter_expression = node.get("Filter", "")
    if "workspace_id" not in filter_expression or "user_id" not in filter_expression:
        raise RuntimeError("HNSW index scan filter is missing a tenant predicate")
    return node


async def _configure_query(
    connection: asyncpg.Connection, *, exact: bool, mode: str = "off"
) -> None:
    await connection.execute("SELECT set_config('hnsw.ef_search', $1, false)", str(EF_SEARCH))
    await connection.execute("SELECT set_config('hnsw.iterative_scan', $1, false)", mode)
    await connection.execute(
        "SELECT set_config('hnsw.max_scan_tuples', $1, false)", str(MAX_SCAN_TUPLES)
    )
    await connection.execute(
        "SELECT set_config('hnsw.scan_mem_multiplier', $1, false)",
        str(SCAN_MEM_MULTIPLIER),
    )
    await connection.execute("SET statement_timeout = '120s'")
    if exact:
        await connection.execute("SET enable_seqscan = on")
        await connection.execute("SET enable_indexscan = off")
        await connection.execute("SET enable_bitmapscan = off")
    else:
        await connection.execute("SET enable_seqscan = off")
        await connection.execute("SET enable_indexscan = on")
        await connection.execute("SET enable_bitmapscan = on")


async def _explain(
    connection: asyncpg.Connection,
    query: str,
    case: WorkloadCase,
    query_vector: str,
    *,
    exact: bool,
    mode: str = "off",
) -> dict[str, Any]:
    await _configure_query(connection, exact=exact, mode=mode)
    row = await connection.fetchrow(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query,
        case.workspace_id,
        case.user_id,
        query_vector,
        TOP_K,
        timeout=DATABASE_OPERATION_TIMEOUT_SECONDS,
    )
    if row is None:
        raise RuntimeError("EXPLAIN returned no result")
    explanation = _parse_explain(row[0])
    names = [node.get("Index Name") for node in _plan_nodes(explanation)]
    if exact and any(name == "spike_s2_items_embedding_hnsw" for name in names):
        raise RuntimeError("exact baseline unexpectedly used the HNSW index")
    if not exact:
        try:
            _validate_hnsw_filter(explanation)
        except RuntimeError as error:
            raise RuntimeError(
                f"invalid HNSW plan for mode={mode}, workspace_id={case.workspace_id}, "
                f"user_id={case.user_id}: {error}"
            ) from error
    return explanation


async def _timed_fetch(
    connection: asyncpg.Connection,
    query: str,
    case: WorkloadCase,
    query_vector: str,
    *,
    exact: bool,
    mode: str = "off",
) -> tuple[list[dict[str, Any]], float]:
    await _configure_query(connection, exact=exact, mode=mode)
    started = time.perf_counter()
    rows = await connection.fetch(
        query,
        case.workspace_id,
        case.user_id,
        query_vector,
        TOP_K,
        timeout=DATABASE_OPERATION_TIMEOUT_SECONDS,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    values = [dict(row) for row in rows]
    assert_filter_confinement(values, case.workspace_id, case.user_id)
    return values, elapsed_ms


async def _create_synthetic_data(connection: asyncpg.Connection) -> int:
    await connection.execute("CREATE EXTENSION vector")
    await connection.execute(
        """
        CREATE TABLE spike_s2_items (
            id integer PRIMARY KEY,
            workspace_id integer NOT NULL,
            user_id integer NOT NULL,
            embedding text NOT NULL
        )
        """
    )
    rng = random.Random(SEED)
    row_id = 1

    async def synthetic_records() -> AsyncIterator[tuple[int, int, int, str]]:
        nonlocal row_id
        weight_total = sum(USER_WEIGHTS)
        for workspace_id, workspace_rows in enumerate(WORKSPACE_SIZES, start=1):
            user_counts = [workspace_rows * weight // weight_total for weight in USER_WEIGHTS]
            user_counts[-1] += workspace_rows - sum(user_counts)
            for user_index, user_count in enumerate(user_counts):
                user_id = workspace_id * 100 + user_index
                for _ in range(user_count):
                    yield (
                        row_id,
                        workspace_id,
                        user_id,
                        vector_literal(random_unit_vector(rng)),
                    )
                    row_id += 1

    await connection.copy_records_to_table(
        "spike_s2_items",
        records=synthetic_records(),
        columns=("id", "workspace_id", "user_id", "embedding"),
        timeout=DATABASE_OPERATION_TIMEOUT_SECONDS * 3,
    )
    await connection.execute(
        "ALTER TABLE spike_s2_items ALTER COLUMN embedding TYPE vector(768) USING embedding::vector"
    )
    await connection.execute(
        """
        CREATE INDEX spike_s2_items_embedding_hnsw
        ON spike_s2_items USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
    await connection.execute("ANALYZE spike_s2_items")
    return row_id - 1


async def _environment(
    connection: asyncpg.Connection, postgres: TemporaryPostgres
) -> dict[str, Any]:
    db_info = await connection.fetchrow(
        """
        SELECT current_setting('server_version') AS postgres_version,
               (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS pgvector_version,
               pg_database_size(current_database()) AS database_size_bytes,
               pg_relation_size('spike_s2_items') AS table_size_bytes,
               pg_relation_size('spike_s2_items_embedding_hnsw') AS hnsw_index_size_bytes
        """
    )
    if db_info is None:
        raise RuntimeError("PostgreSQL returned no environment measurements")
    docker_version = _docker(
        ["version", "--format", "{{.Server.Version}}"],
        label="read Docker server version",
    ).stdout.strip()
    stats = _docker(
        [
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}|{{.CPUPerc}}",
            postgres.container_name,
        ],
        label="read benchmark container resource snapshot",
    ).stdout.strip()
    memory_usage, _, cpu_percent = stats.partition("|")
    return {
        "host_platform": platform.platform(),
        "python_version": platform.python_version(),
        "asyncpg_version": asyncpg.__version__,
        "asyncpg_statement_cache_size": 0,
        "docker_server_version": docker_version,
        "image": IMAGE,
        "container_memory_limit": CONTAINER_MEMORY,
        "container_cpu_limit": CONTAINER_CPUS,
        "container_memory_usage_snapshot": memory_usage,
        "container_cpu_percent_snapshot": cpu_percent,
        "postgres_version": db_info["postgres_version"],
        "pgvector_version": db_info["pgvector_version"],
        "database_size_bytes": db_info["database_size_bytes"],
        "table_size_bytes": db_info["table_size_bytes"],
        "hnsw_index_size_bytes": db_info["hnsw_index_size_bytes"],
        "benchmark_host_port": postgres.host_port,
    }


async def run_benchmark(
    repetitions: int = REPETITIONS, warmups: int = WARMUP_RUNS
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    postgres = TemporaryPostgres()
    connection: asyncpg.Connection | None = None
    environment: dict[str, Any] = {}
    measurements: list[dict[str, Any]] = []
    exact_measurements: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    plan_probe_measurements: list[dict[str, Any]] = []
    try:
        postgres.start()
        connection = await postgres.connect()
        total_rows = await _create_synthetic_data(connection)
        if total_rows != sum(WORKSPACE_SIZES):
            raise RuntimeError("synthetic dataset row count did not match configuration")

        query_rng = random.Random(SEED ^ 0x5A17)
        for case in workload_cases():
            exact_ids: list[list[int]] = []
            query_literals: list[str] = []
            for query_index in range(2):
                query_vector = vector_literal(random_unit_vector(query_rng))
                query_literals.append(query_vector)
                exact_plan = await _explain(connection, EXACT_QUERY, case, query_vector, exact=True)
                if query_index == 0:
                    plans.append(
                        {
                            "workspace_id": case.workspace_id,
                            "user_id": case.user_id,
                            "strategy": "exact",
                            "plan": exact_plan,
                        }
                    )
                measured_times: list[float] = []
                exact_rows: list[dict[str, Any]] = []
                exact_index_scan_node = next(
                    (
                        node
                        for node in _plan_nodes(exact_plan)
                        if node.get("Node Type") in ("Seq Scan", "Index Scan")
                    ),
                    None,
                )
                if exact_index_scan_node is None:
                    raise RuntimeError("exact ground-truth plan has no scan node")
                if exact_index_scan_node.get("Node Type") != "Seq Scan":
                    raise RuntimeError("exact ground-truth plan is not a sequential scan")
                for _ in range(repetitions):
                    exact_rows, elapsed = await _timed_fetch(
                        connection,
                        EXACT_QUERY,
                        case,
                        query_vector,
                        exact=True,
                    )
                    measured_times.append(elapsed)
                exact_ids.append([row["id"] for row in exact_rows])
                exact_measurements.append(
                    {
                        "workspace_id": case.workspace_id,
                        "user_id": case.user_id,
                        "query_index": query_index,
                        "latency_samples_ms": [round(value, 4) for value in measured_times],
                        **_sample_percentiles(measured_times),
                        "returned_rows": len(exact_rows),
                        "ground_truth_ids": [row["id"] for row in exact_rows],
                    }
                )

            for mode in ITERATIVE_SCAN_MODES:
                query_samples: list[float] = []
                query_recalls: list[float] = []
                query_returned_counts: list[int] = []
                for query_index, query_vector in enumerate(query_literals):
                    plan = await _explain(
                        connection, ANN_QUERY, case, query_vector, exact=False, mode=mode
                    )
                    plans.append(
                        {
                            "workspace_id": case.workspace_id,
                            "user_id": case.user_id,
                            "query_index": query_index,
                            "strategy": mode,
                            "plan": plan,
                        }
                    )
                    hnsw_node = _validate_hnsw_filter(plan)
                    plan_rows = hnsw_node["Actual Rows"]
                    probe_rows, _ = await _timed_fetch(
                        connection,
                        ANN_QUERY,
                        case,
                        query_vector,
                        exact=False,
                        mode=mode,
                    )
                    if len(probe_rows) != plan_rows:
                        raise RuntimeError(
                            "EXPLAIN row count disagrees with direct probe for "
                            f"mode={mode}, workspace_id={case.workspace_id}, "
                            f"user_id={case.user_id}, query_index={query_index}: "
                            f"EXPLAIN={plan_rows}, direct={len(probe_rows)}"
                        )
                    plan_probe_measurements.append(
                        {
                            "workspace_id": case.workspace_id,
                            "user_id": case.user_id,
                            "query_index": query_index,
                            "strategy": mode,
                            "hnsw_actual_rows": plan_rows,
                            "direct_probe_rows": len(probe_rows),
                            "rows_removed_by_filter": hnsw_node.get("Rows Removed by Filter", 0),
                            "filter": hnsw_node.get("Filter"),
                        }
                    )
                    for _ in range(warmups):
                        await _timed_fetch(
                            connection,
                            ANN_QUERY,
                            case,
                            query_vector,
                            exact=False,
                            mode=mode,
                        )
                    for _ in range(repetitions):
                        rows, elapsed = await _timed_fetch(
                            connection,
                            ANN_QUERY,
                            case,
                            query_vector,
                            exact=False,
                            mode=mode,
                        )
                        query_samples.append(elapsed)
                        query_recalls.append(
                            recall_at_k(
                                exact_ids[query_index],
                                [row["id"] for row in rows],
                                TOP_K,
                            )
                        )
                        query_returned_counts.append(len(rows))
                measurements.append(
                    {
                        "workspace_id": case.workspace_id,
                        "user_id": case.user_id,
                        "workspace_selectivity": case.workspace_selectivity,
                        "user_selectivity_within_workspace": case.user_selectivity_within_workspace,
                        "combined_selectivity": case.combined_selectivity,
                        "matching_rows": case.matching_rows,
                        "strategy": mode,
                        "ef_search": EF_SEARCH,
                        "max_scan_tuples": MAX_SCAN_TUPLES,
                        "scan_mem_multiplier": SCAN_MEM_MULTIPLIER,
                        "query_count": len(query_literals),
                        "latency_samples_ms": [round(value, 4) for value in query_samples],
                        **_sample_percentiles(query_samples),
                        "recall_samples": query_recalls,
                        "mean_recall_at_k": round(statistics.fmean(query_recalls), 6),
                        "returned_row_count_samples": query_returned_counts,
                    }
                )
        environment = await _environment(connection, postgres)
    finally:
        try:
            if connection is not None:
                await connection.close()
        finally:
            cleanup_failures = postgres.cleanup()
        if cleanup_failures:
            raise RuntimeError("; ".join(cleanup_failures))

    return {
        "schema_version": 1,
        "benchmark": "S2 filtered pgvector HNSW synthetic spike",
        "started_at_utc": started_at.isoformat(),
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "configuration": {
            "dimensions": DIMENSIONS,
            "top_k": TOP_K,
            "total_rows": sum(WORKSPACE_SIZES),
            "workspace_sizes": list(WORKSPACE_SIZES),
            "user_weights": list(USER_WEIGHTS),
            "query_user_indices": list(QUERY_USER_INDICES),
            "query_vectors_per_case": 2,
            "repetitions": repetitions,
            "warmup_runs_per_ann_query": warmups,
            "strategies": list(ITERATIVE_SCAN_MODES),
            "exact_baseline": (
                "Same predicates/order; indexscan and bitmapscan disabled; "
                "EXPLAIN checked for a sequential scan and no HNSW index."
            ),
            "ann_planner_setting": (
                "enable_seqscan=off to require an HNSW path for the index-only comparison"
            ),
            "per_query_settings": (
                "pgvector and planner GUCs are explicitly set before every probe and timed query"
            ),
            "asyncpg_statement_cache_size": 0,
            "ann_predicates": ["workspace_id = $1", "user_id = $2"],
            "distance_metric": "cosine",
            "index": "HNSW vector_cosine_ops; m=16; ef_construction=64",
            "query_seed": SEED ^ 0x5A17,
        },
        "environment": environment,
        "exact_baseline_measurements": exact_measurements,
        "ann_measurements": measurements,
        "plan_probe_measurements": plan_probe_measurements,
        "explain_plans": plans,
        "cleanup": {
            "temporary_container_removed": True,
            "temporary_volume_removed": True,
            "external_services_used": False,
            "application_database_touched": False,
        },
        "limitations": [
            (
                "Synthetic isotropic Gaussian vectors and tenant distributions are not "
                "representative production data."
            ),
            (
                "Client latency includes loopback and asyncpg round-trip overhead; retained "
                "EXPLAIN ANALYZE plans include server execution and buffer counts."
            ),
            (
                "No RLS policies are installed or evaluated; query predicates are not "
                "evidence of RLS enforcement."
            ),
            (
                "No user-approved recall or latency target exists; measurements are "
                "observations, not pass/fail gates."
            ),
            (
                "No partial index was added; this synthetic workload does not establish a "
                "stable production predicate or index maintenance trade-off."
            ),
            (
                "The container resource snapshot is sampled after benchmark queries, "
                "not a peak-resource profile."
            ),
        ],
    }


def _output_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "architecture"
        / "SPIKE_S2_VECTOR.measurements.json"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="explicit opt-in; creates disposable synthetic Docker resources",
    )
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--warmups", type=int, default=WARMUP_RUNS)
    arguments = parser.parse_args(argv)
    if not arguments.run:
        parser.error("benchmark is opt-in; pass --run to proceed")
    if not 1 <= arguments.repetitions <= 20:
        parser.error("--repetitions must be between 1 and 20")
    if not 0 <= arguments.warmups <= 5:
        parser.error("--warmups must be between 0 and 5")

    try:
        result = asyncio.run(
            asyncio.wait_for(
                run_benchmark(arguments.repetitions, arguments.warmups),
                timeout=BENCHMARK_TIMEOUT_SECONDS,
            )
        )
        output_path = _output_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".SPIKE_S2_VECTOR.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as output_file:
                output_file.write(json.dumps(result, indent=2, sort_keys=True) + os.linesep)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except (OSError, RuntimeError, asyncpg.PostgresError, TimeoutError) as error:
        if isinstance(error, RuntimeError):
            reason = str(error)
        elif isinstance(error, asyncpg.PostgresError):
            reason = f"PostgreSQL error SQLSTATE {error.sqlstate or 'unknown'}"
        else:
            reason = type(error).__name__
        print(f"S2 benchmark failed safely: {reason}", file=sys.stderr)
        return 1
    print(f"S2 benchmark complete; results written to {_output_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
