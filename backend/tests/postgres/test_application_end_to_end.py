"""The real application factory: real lifespan, PostgreSQL role guard, Redis limiter and routes.

Only the outgoing mail is replaced (a recording sender) so the one-time codes can be read.
"""

import os
import secrets
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.db.tenant import UnsafeDatabaseRole
from app.main import create_app
from tests.conftest import TestDatabase
from tests.support.auth import GOOD_PASSWORD, FakeEmailSender
from tests.support.settings import make_settings

pytestmark = [pytest.mark.postgres, pytest.mark.redis]

BASE = "/api/v1/auth"


def redis_url() -> str:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")
    return url


def settings_for(database_url: str):  # type: ignore[no-untyped-def]
    # Fresh keys per run, so rate-limit counters left in Redis by an earlier run cannot collide.
    return make_settings(
        database_url=database_url,
        redis_url=redis_url(),
        otp_hmac_key=secrets.token_urlsafe(48),
        jwt_signing_key=secrets.token_urlsafe(48),
    )


@pytest.fixture
def mail() -> Iterator[FakeEmailSender]:
    sender = FakeEmailSender()
    with patch("app.auth.factory.build_email_sender", return_value=sender):
        yield sender


def test_account_lifecycle_through_the_production_factory(
    migrated_database: TestDatabase, mail: FakeEmailSender
) -> None:
    app = create_app(settings_for(migrated_database.app_url.render_as_string(hide_password=False)))
    suffix = secrets.token_hex(6)
    email = f"e2e.{suffix}@example.com"
    with TestClient(app) as client:
        assert client.get("/api/v1/readyz").json() == {"status": "ready"}
        body = {
            "username": f"e2e_{suffix}",
            "email": email,
            "password": GOOD_PASSWORD,
            "full_name": "End To End",
        }
        assert client.post(f"{BASE}/register", json=body).status_code == 202
        login = {"username": body["username"], "password": GOOD_PASSWORD}
        assert client.post(f"{BASE}/token", json=login).status_code == 403  # not verified yet
        code = mail.codes_for(email)[-1]
        assert (
            client.post(
                f"{BASE}/verify-email",
                json={"email": email, "code": code, "password": GOOD_PASSWORD},
            ).status_code
            == 200
        )
        tokens = client.post(
            f"{BASE}/token", json={"username": body["username"], "password": GOOD_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        me = client.get(f"{BASE}/me", headers=headers)
        assert me.status_code == 200 and me.json()["role"] == "admin"
        rotated = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert rotated.status_code == 200
        replay = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert replay.status_code == 401
        assert client.get(f"{BASE}/me", headers=headers).status_code == 401  # session revoked


def test_startup_refuses_to_run_as_the_administrator_role(
    migrated_database: TestDatabase, mail: FakeEmailSender
) -> None:
    """A runtime URL that points at the migration role would silently disable row-level security."""
    app = create_app(
        settings_for(migrated_database.admin_url.render_as_string(hide_password=False))
    )
    with pytest.raises(UnsafeDatabaseRole), TestClient(app):
        pytest.fail("The application must not start as a role that bypasses row-level security")
