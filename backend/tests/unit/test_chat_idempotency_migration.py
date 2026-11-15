"""Offline SQL contract checks for durable chat idempotency."""

import re
from pathlib import Path

from app.db.repositories import chat

MIGRATION = (
    Path(__file__).parents[2]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "0007_chat_idempotency.py"
)


def test_migration_creates_scoped_digest_only_durable_claims_with_forced_rls() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0007"' in source
    assert 'down_revision: str | None = "0006"' in source
    assert "CREATE TABLE chat_idempotency" in source
    assert "PRIMARY KEY (workspace_id, user_id, key_digest)" in source
    assert "FOREIGN KEY (workspace_id, user_id)" in source
    assert "REFERENCES users (workspace_id, id) ON DELETE CASCADE" in source
    assert "key_digest VARCHAR(64) NOT NULL" in source
    assert "request_hash VARCHAR(64) NOT NULL" in source
    assert "key_digest ~ '^[0-9a-f]{64}$'" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('app.workspace_id', true)" in source
    assert "current_setting('app.user_id', true)" in source
    assert "CREATE POLICY ws_isolation" in source
    assert "GRANT SELECT, INSERT ON chat_idempotency TO maya_app" in source
    assert "GRANT UPDATE (state, session_id, finish_reason, error_code, updated_at)" in source
    assert "GRANT DELETE" not in source
    assert re.search(r"\b(?:idempotency_key|raw_key|content)\s+(?:TEXT|VARCHAR)", source) is None


def test_repository_claim_is_unique_and_finalization_is_owned_compare_and_set() -> None:
    claim_sql = str(chat.INSERT_IDEMPOTENCY)
    save_sql = str(chat.SAVE_IDEMPOTENCY)

    assert "ON CONFLICT (workspace_id, user_id, key_digest) DO NOTHING" in claim_sql
    assert "WHERE workspace_id = :workspace_id AND user_id = :user_id" in save_sql
    assert "key_digest = :key_digest" in save_sql
    assert "request_hash = :request_hash" in save_sql
    assert "assistant_message_id = :assistant_message_id" in save_sql
    assert "state = 'in_progress'" in save_sql
    assert "RETURNING key_digest" in save_sql
