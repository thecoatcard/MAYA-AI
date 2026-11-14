"""HTTP surface of the local-owner bootstrap (ADR-0014): refusals look identical."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.events import LoggingAuthEvents
from app.main import create_app
from tests.support.auth import GOOD_PASSWORD, Harness, build_harness
from tests.support.fake_store import FakeAuthStore
from tests.support.settings import make_settings

STATUS = "/api/v1/auth/bootstrap/status"
BOOTSTRAP = "/api/v1/auth/bootstrap"
BODY = {"full_name": "Local Owner", "email": "owner@example.com", "password": GOOD_PASSWORD}
COOKIE_TRANSPORT = {"X-Maya-Refresh-Transport": "cookie"}


def client_for(harness: Harness, peer: str) -> Iterator[TestClient]:
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        app = create_app(make_settings(), auth_service=harness.service)
        with TestClient(app, client=(peer, 50000)) as client:
            yield client


@pytest.fixture
def local() -> Iterator[TestClient]:
    yield from client_for(
        build_harness(FakeAuthStore(), local_owner_bootstrap=True, events=LoggingAuthEvents()),
        "127.0.0.1",
    )


@pytest.fixture
def remote() -> Iterator[TestClient]:
    yield from client_for(build_harness(FakeAuthStore(), local_owner_bootstrap=True), "203.0.113.9")


@pytest.fixture
def disabled() -> Iterator[TestClient]:
    yield from client_for(build_harness(FakeAuthStore()), "127.0.0.1")


def test_status_reports_available_on_an_empty_local_install(local: TestClient) -> None:
    response = local.get(STATUS)
    assert response.status_code == 200
    assert response.json() == {"available": True, "reason": None}
    assert response.headers["cache-control"] == "no-store"


def test_bootstrap_returns_the_login_response_shape_and_signs_in(local: TestClient) -> None:
    response = local.post(BOOTSTRAP, json=BODY)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert response.headers["cache-control"] == "no-store"
    me = local.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["role"] == "admin"
    # /auth/me carries the caller's own email and name for display (additive fields).
    assert me.json()["email"] == BODY["email"]
    assert me.json()["display_name"] == BODY["full_name"]
    assert local.get(STATUS).json() == {"available": False, "reason": "already_initialised"}


def test_cookie_transport_sets_the_refresh_cookie_and_hides_the_token() -> None:
    harness = build_harness(FakeAuthStore(), local_owner_bootstrap=True)
    for client in client_for(harness, "127.0.0.1"):
        response = client.post(BOOTSTRAP, json=BODY, headers=COOKIE_TRANSPORT)
        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        cookie = response.headers["set-cookie"]
        assert "maya_refresh=" in cookie and "HttpOnly" in cookie


def test_second_call_is_refused_and_creates_nothing(local: TestClient) -> None:
    assert local.post(BOOTSTRAP, json=BODY).status_code == 200
    second = local.post(BOOTSTRAP, json={**BODY, "email": "intruder@example.com"})
    assert second.status_code == 404
    assert second.json() == {"error": "bootstrap_unavailable"}
    login = local.post(
        "/api/v1/auth/token", json={"username": "intruder@example.com", "password": GOOD_PASSWORD}
    )
    assert login.status_code == 401


def test_every_refusal_is_the_same_answer_so_nothing_can_be_enumerated(
    local: TestClient, remote: TestClient, disabled: TestClient
) -> None:
    assert local.post(BOOTSTRAP, json=BODY).status_code == 200  # now a user exists
    refusals: list[Any] = [
        local.post(BOOTSTRAP, json=BODY),  # user exists
        remote.post(BOOTSTRAP, json=BODY),  # not loopback
        disabled.post(BOOTSTRAP, json=BODY),  # flag off
    ]
    assert {(r.status_code, r.text) for r in refusals} == {
        (404, '{"error":"bootstrap_unavailable"}')
    }
    # The status of a non-local peer or a disabled install says nothing about the database.
    assert remote.get(STATUS).json() == disabled.get(STATUS).json()
    assert remote.get(STATUS).json() == {"available": False, "reason": "disabled"}


def test_flag_off_and_non_loopback_never_create_an_account(
    remote: TestClient, disabled: TestClient
) -> None:
    assert remote.post(BOOTSTRAP, json=BODY).status_code == 404
    assert disabled.post(BOOTSTRAP, json=BODY).status_code == 404
    login = remote.post("/api/v1/auth/token", json={"username": "owner", "password": GOOD_PASSWORD})
    assert login.status_code == 401


def test_a_forwarded_for_header_cannot_make_a_remote_peer_local(remote: TestClient) -> None:
    response = remote.post(BOOTSTRAP, json=BODY, headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 404
    assert remote.get(STATUS, headers={"X-Forwarded-For": "127.0.0.1"}).json()["available"] is False


def test_password_policy_is_enforced_with_the_registration_error_codes(local: TestClient) -> None:
    response = local.post(BOOTSTRAP, json={**BODY, "password": "short"})
    assert response.status_code == 422
    assert response.json() == {"error": "password_too_short"}
    assert local.get(STATUS).json()["available"] is True


def test_unknown_fields_and_bad_email_are_validation_errors(local: TestClient) -> None:
    assert local.post(BOOTSTRAP, json={**BODY, "role": "admin"}).status_code == 422
    assert local.post(BOOTSTRAP, json={**BODY, "email": "nope"}).status_code == 422
