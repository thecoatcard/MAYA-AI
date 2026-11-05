"""The documented migration commands, run as a real subprocess against a disposable database."""

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import BACKEND_DIR, TestDatabase

pytestmark = pytest.mark.postgres


def alembic(arguments: list[str], environment: dict[str, str]) -> int:
    # Output is intentionally not echoed: connection errors can mention the target.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return completed.returncode


async def table_count(database: TestDatabase) -> int:
    engine = create_async_engine(database.admin_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    import os

    for name in list(os.environ):
        if name.startswith("MAYA_"):
            monkeypatch.delenv(name)
    return dict(os.environ)


async def test_env_file_option_upgrades_and_downgrades(
    fresh_database: TestDatabase, tmp_path: Path, clean_environment: dict[str, str]
) -> None:
    env_file = tmp_path / "migrate.env"
    env_file.write_text(
        f"MAYA_MIGRATION_DATABASE_URL={fresh_database.admin_url.render_as_string(hide_password=False)}\n"
    )
    option = ["-x", f"env_file={env_file}"]
    assert alembic([*option, "upgrade", "head"], clean_environment) == 0
    assert await table_count(fresh_database) == 14
    assert alembic([*option, "downgrade", "base"], clean_environment) == 0
    assert await table_count(fresh_database) == 0
    assert alembic([*option, "upgrade", "head"], clean_environment) == 0
    assert await table_count(fresh_database) == 14


async def test_process_environment_variable_is_also_accepted(
    fresh_database: TestDatabase, clean_environment: dict[str, str]
) -> None:
    environment = clean_environment | {
        "MAYA_MIGRATION_DATABASE_URL": fresh_database.admin_url.render_as_string(
            hide_password=False
        )
    }
    assert alembic(["upgrade", "head"], environment) == 0
    assert await table_count(fresh_database) == 14


def test_a_missing_or_invalid_url_fails_without_touching_anything(
    clean_environment: dict[str, str], tmp_path: Path
) -> None:
    assert alembic(["upgrade", "head"], clean_environment) != 0
    bad = tmp_path / "bad.env"
    bad.write_text("MAYA_MIGRATION_DATABASE_URL=postgresql://wrong-driver/db\n")
    assert alembic(["-x", f"env_file={bad}", "upgrade", "head"], clean_environment) != 0


def test_offline_sql_generation_is_refused(clean_environment: dict[str, str]) -> None:
    assert alembic(["upgrade", "head", "--sql"], clean_environment) != 0
