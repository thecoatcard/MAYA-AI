"""Revision 0016 repairs the approval_requests UPDATE grants the execution lifecycle needs."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "app" / "db" / "migrations" / "versions"
LIFECYCLE_WRITES = {
    "claimed_by",
    "claimed_at",
    "started_at",
    "completed_at",
    "failed_at",
    "failure_reason",
    "execution_output",
}


def _load() -> ModuleType:
    path = VERSIONS / "0016_repair_approval_grants.py"
    spec = importlib.util.spec_from_file_location("migration_0016", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_continues_from_0015() -> None:
    module = _load()
    assert module.revision == "0016"
    assert module.down_revision == "0015"


def test_grants_cover_every_lifecycle_column_the_worker_writes() -> None:
    module = _load()
    assert set(module.LIFECYCLE_COLUMNS) == LIFECYCLE_WRITES


def test_the_worker_only_writes_columns_that_the_grants_cover() -> None:
    """Guards the root cause: a column the worker updates but nobody granted fails with
    'permission denied for table approval_requests' and the approved task never progresses."""
    module = _load()
    granted = set(module.LIFECYCLE_COLUMNS) | {
        "status",
        "decided_at",
        "approval_token",
        "decision_channel",
        "token_jti",
        "consumed_at",
        "tool_args",
    }
    app = Path(__file__).resolve().parents[2] / "app"
    written: set[str] = set()
    for source in app.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for statement in re.findall(r"UPDATE approval_requests(.*?)(?:WHERE|\"\"\")", text, re.S):
            written.update(
                re.findall(r"\b([a-z_]+)\s*=\s*(?::|clock_timestamp|now|CAST|')", statement)
            )
    written.discard("status")
    unknown = {name for name in written if name not in granted and name != "expires_at"}
    assert unknown == set(), f"approval_requests columns written but never granted: {unknown}"


def test_the_migration_is_additive_and_least_privilege() -> None:
    source = (VERSIONS / "0016_repair_approval_grants.py").read_text(encoding="utf-8")
    assert "GRANT UPDATE (" in source
    assert "ON approval_requests TO maya_app" in source
    assert "GRANT DELETE" not in source
    assert "BYPASSRLS" not in source
    assert "DISABLE ROW LEVEL SECURITY" not in source


@pytest.mark.parametrize("column", sorted(LIFECYCLE_WRITES))
def test_every_lifecycle_column_is_named_in_the_upgrade(column: str) -> None:
    module = _load()
    assert column in module.LIFECYCLE_COLUMNS
