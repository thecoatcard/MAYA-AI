"""Seed a default verified local development user and workspace.

Usage:
    python backend/scripts/seed_dev_user.py
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

import asyncpg  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.security import PasswordService  # noqa: E402

DEFAULT_USERNAME = "admin"
DEFAULT_EMAIL = "admin@example.com"
DEFAULT_PASSWORD = "AdminPassword123!"  # secret-scan:allow (local development seed script)
DEFAULT_FULL_NAME = "Maya Administrator"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_WORKSPACE_SLUG = "default-ws"


async def seed_dev_user() -> None:
    settings = Settings(_env_file=str(backend_dir.parent / ".env.example"))
    mig_url = settings.migration_database_url or settings.database_url
    db_url = mig_url.get_secret_value().replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(db_url)
    try:
        existing = await conn.fetchrow(
            "SELECT id, username FROM users WHERE username = $1", DEFAULT_USERNAME
        )
        if existing:
            print(f"User '{DEFAULT_USERNAME}' already exists with ID: {existing['id']}")
            return

        passwords = PasswordService()
        hashed = await passwords.hash(DEFAULT_PASSWORD)
        now = datetime.now(UTC)
        ws_id = uuid4()
        user_id = uuid4()

        await conn.execute(
            """INSERT INTO workspaces (id, name, slug, created_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (slug) DO NOTHING""",
            ws_id,
            DEFAULT_WORKSPACE_NAME,
            DEFAULT_WORKSPACE_SLUG,
            now,
        )

        ws_row = await conn.fetchrow(
            "SELECT id FROM workspaces WHERE slug = $1", DEFAULT_WORKSPACE_SLUG
        )
        actual_ws_id = ws_row["id"] if ws_row else ws_id

        await conn.execute(
            """INSERT INTO users (
                   id, workspace_id, username, email, full_name,
                   hashed_password, email_verified_at, password_changed_at,
                   role, created_at
               )
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            user_id,
            actual_ws_id,
            DEFAULT_USERNAME,
            DEFAULT_EMAIL,
            DEFAULT_FULL_NAME,
            hashed,
            now,
            now,
            "admin",
            now,
        )
        print(f"Successfully seeded user '{DEFAULT_USERNAME}' ({DEFAULT_EMAIL}) with ID: {user_id}")
        await passwords.aclose()
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed_dev_user())
