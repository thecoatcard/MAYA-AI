"""Reproducible, opt-in Celery-versus-ARQ benchmark for Phase 01 S1.

The default mode prints a plan and never starts containers. Only ``--run`` may
build a pinned spike image or create isolated, temporary Docker resources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent / "s1"
if not __package__:
    # Direct-file execution needs the backend package root, not a second alias
    # for the spikes directory. Package imports use the same namespace as tests.
    sys.path.insert(0, str(SPIKE_ROOT.parents[2]))

from tests.spikes.s1.orchestrator import RunOptions, run_sandbox  # noqa: E402

LOCK_PATH = SPIKE_ROOT / "uv.lock"
CELERY_COMPAT_LOCK_PATH = SPIKE_ROOT / "celery_compat" / "uv.lock"
DEPENDENCIES = {
    "arq": "0.28.0",
    "asyncpg": "0.32.0",
    "celery": "5.6.3",
    "langgraph": "1.2.20",
    "redis-py benchmark": "5.3.1",
    "backend runtime redis-py": "8.1.0",
}
REDIS_COMPATIBILITY = {
    "arq_0.28.0": "redis-py>=4.2.0,<6",
    "celery_5.6.3_redis_extra": "redis-py>=4.5.2,<6.5 (excluding 4.5.5 and 5.0.2)",
    "backend_runtime": "redis-py==8.1.0; incompatible with both extras",
    "shared_benchmark_client": "redis-py==5.3.1",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute a bounded benchmark in newly created isolated Docker resources",
    )
    parser.add_argument("--jobs", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--candidate",
        choices=("comparison", "celery-compat"),
        default="comparison",
        help="use the historical shared lock or the isolated redis-py 8-compatible Celery lock",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new JSON output path; existing files are never overwritten",
    )
    return parser


def _lock_sha256() -> str:
    return hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()


def _compat_lock_sha256() -> str:
    return hashlib.sha256(CELERY_COMPAT_LOCK_PATH.read_bytes()).hexdigest()


def _default_output_path(run_id: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return SPIKE_ROOT / "results" / f"s1-queue-{stamp}-{run_id}.json"


def _write_new_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def _print_plan(jobs: int, seed: int, timeout: int) -> None:
    _print_plan_for_candidate(jobs, seed, timeout, "comparison")


def _print_plan_for_candidate(jobs: int, seed: int, timeout: int, candidate: str) -> None:
    compat = candidate == "celery-compat"
    dependencies = (
        {
            "celery": "5.6.3",
            "kombu": "5.7.0",
            "redis-py": "8.1.0",
            "asyncpg": "0.32.0",
            "langgraph": "1.2.20",
        }
        if compat
        else DEPENDENCIES
    )
    plan = {
        "status": "plan_only",
        "candidate": candidate,
        "run_command": (
            "python backend/tests/spikes/spike_s1_queue.py "
            + ("--candidate celery-compat " if compat else "")
            + f"--run --jobs {jobs} --seed {seed} --timeout-seconds {timeout}"
        ),
        "backend_order": ["Celery 5.6.3"] if compat else ["Celery 5.6.3", "ARQ 0.28.0"],
        "workload": (
            "Three async LangGraph nodes per synthetic task, each yielding for "
            "a seeded 8-15 ms interval; Redis queue; disposable PostgreSQL receipts."
        ),
        "resource_limits": {"worker_driver_cpus": 1.0, "worker_driver_memory": "768m"},
        "network": "new internal Docker network; no published ports or host mounts",
        "dependencies": dependencies,
        "lock_file": (
            "backend/tests/spikes/s1/celery_compat/uv.lock"
            if compat
            else "backend/tests/spikes/s1/uv.lock"
        ),
        "redis_compatibility": (
            "Declared-compatible candidate; transport and worker behavior remain unverified."
            if compat
            else REDIS_COMPATIBILITY
        ),
        "ordinary_pytest": "does not invoke this runner or create Docker resources",
    }
    print(json.dumps(plan, indent=2, sort_keys=True))


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = RunOptions(
        jobs=args.jobs,
        seed=args.seed,
        timeout_seconds=args.timeout_seconds,
        output_path=args.output.resolve() if args.output else Path(""),
    )
    options.validate()
    if not args.run:
        if args.output is not None:
            raise ValueError("--output is valid only with --run")
        _print_plan_for_candidate(args.jobs, args.seed, args.timeout_seconds, args.candidate)
        return 0

    output_path = args.output.resolve() if args.output else None
    if output_path is not None and output_path.exists():
        raise FileExistsError("output path already exists; choose a new path")
    data = run_sandbox(
        SPIKE_ROOT,
        jobs=options.jobs,
        seed=options.seed,
        timeout_seconds=options.timeout_seconds,
        candidate=args.candidate,
    )
    data["invocation"] = {
        "argv": [
            "python",
            "backend/tests/spikes/spike_s1_queue.py",
            "--run",
            "--jobs",
            str(options.jobs),
            "--seed",
            str(options.seed),
            "--timeout-seconds",
            str(options.timeout_seconds),
            "--candidate",
            args.candidate,
        ],
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host_platform": platform.platform(),
        "host_python": sys.version.split()[0],
        "uv": "0.12.17",
        "candidate": args.candidate,
        "dependencies": (
            {
                "celery": "5.6.3",
                "kombu": "5.7.0",
                "redis-py": "8.1.0",
                "asyncpg": "0.32.0",
                "langgraph": "1.2.20",
            }
            if args.candidate == "celery-compat"
            else DEPENDENCIES
        ),
        "redis_compatibility": (
            {
                "celery_5.6.3_kombu_5.7.0": "redis-py>=6.4.0,<9.0.0",
                "candidate_redis_py": "8.1.0",
                "transport_validation": "completed in this isolated run",
            }
            if args.candidate == "celery-compat"
            else REDIS_COMPATIBILITY
        ),
        "lock_file": (
            "backend/tests/spikes/s1/celery_compat/uv.lock"
            if args.candidate == "celery-compat"
            else "backend/tests/spikes/s1/uv.lock"
        ),
        "lock_sha256": (
            _compat_lock_sha256() if args.candidate == "celery-compat" else _lock_sha256()
        ),
        "output_path": str(output_path) if output_path else None,
    }
    target = output_path or _default_output_path(
        f"celery-compat-{data['run_id']}"
        if args.candidate == "celery-compat"
        else str(data["run_id"])
    )
    data["invocation"]["output_path"] = str(target)
    _write_new_json(target, data)
    print(
        json.dumps(
            {
                "status": data["status"],
                "run_id": data["run_id"],
                "output": str(target),
                "cleanup": data.get("cleanup"),
                "error": data.get("error"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if data["status"] == "completed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except (ValueError, FileExistsError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
