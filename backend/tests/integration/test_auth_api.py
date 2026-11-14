"""The HTTP surface of authentication, with an in-memory store (deterministic, no services)."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.events import LoggingAuthEvents
from app.main import create_app
from tests.support.auth import GOOD_PASSWORD, NEW_PASSWORD, Harness, build_harness
from tests.support.fake_store import FakeAuthStore
from tests.support.settings import make_settings

BASE = "/api/v1/auth"
COOKIE_TRANSPORT = {"X-Maya-Refresh-Transport": "cookie"}
HEADERS_THAT_MUST_MATCH = ("content-type", "content-length")


@dataclass
class Api:
    client: TestClient
    harness: Harness
    app: FastAPI

    def post(self, path: str, **body: Any) -> Any:
        return self.client.post(f"{BASE}/{path}", json=body)

    def registration(self, **overrides: str) -> dict[str, str]:
        username, email = self.harness.identity()
        body = {
            "username": username,
            "email": email,
            "password": GOOD_PASSWORD,
            "full_name": "Test Person",
        }
        body.update(overrides)
        return body

    def register(self, **overrides: str) -> dict[str, str]:
        body = self.registration(**overrides)
        assert self.client.post(f"{BASE}/register", json=body).status_code == 202
        return body

    def register_and_verify(self, **overrides: str) -> dict[str, str]:
        body = self.register(**overrides)
        code = self.harness.email.codes_for(body["email"].lower())[-1]
        assert (
            self.post(
                "verify-email", email=body["email"], code=code, password=GOOD_PASSWORD
            ).status_code
            == 200
        )
        return body

    def login(self, body: dict[str, str]) -> dict[str, Any]:
        response = self.post("token", username=body["username"], password=body["password"])
        assert response.status_code == 200
        return dict(response.json())


def make_api(
    *, email: bool = True, settings_overrides: dict[str, Any] | None = None
) -> Iterator[Api]:
    harness = build_harness(FakeAuthStore(), email=email, events=LoggingAuthEvents())
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        app = create_app(make_settings(**(settings_overrides or {})), auth_service=harness.service)
        with TestClient(app) as client:
            yield Api(client, harness, app)


@pytest.fixture
def api() -> Iterator[Api]:
    yield from make_api()


@pytest.fixture
def api_without_email() -> Iterator[Api]:
    yield from make_api(email=False)


@pytest.fixture
def api_with_insecure_cookie() -> Iterator[Api]:
    yield from make_api(settings_overrides={"auth_cookie_secure": False})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def refresh_cookie(response: Any) -> str:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return parsed["maya_refresh"].value


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"maya_refresh={token}"}


def test_the_public_surface_is_exactly_the_documented_endpoints(api: Api) -> None:
    schema = api.app.openapi()
    paths = {
        (path, method.upper())
        for path, operations in schema["paths"].items()
        for method in operations
    }
    assert paths == {
        ("/api/v1/healthz", "GET"),
        ("/api/v1/readyz", "GET"),
        (f"{BASE}/identify", "POST"),
        (f"{BASE}/bootstrap", "POST"),
        (f"{BASE}/bootstrap/status", "GET"),
        (f"{BASE}/signup/otp", "POST"),
        (f"{BASE}/signup/complete", "POST"),
        (f"{BASE}/register", "POST"),
        (f"{BASE}/resend-verification", "POST"),
        (f"{BASE}/verify-email", "POST"),
        (f"{BASE}/token", "POST"),
        (f"{BASE}/forgot-password", "POST"),
        (f"{BASE}/reset-password", "POST"),
        (f"{BASE}/refresh", "POST"),
        (f"{BASE}/logout", "POST"),
        (f"{BASE}/me", "GET"),
        ("/api/v1/chat/message", "POST"),
        # Scheduled reports (docs/api/SCHEDULES.md, ADR-0033): added on purpose.
        ("/api/v1/schedules", "GET"),
        ("/api/v1/schedules", "POST"),
        ("/api/v1/schedules/updates", "GET"),
        ("/api/v1/schedules/updates/{schedule_id}/ack", "POST"),
        ("/api/v1/schedules/settings", "PUT"),
        ("/api/v1/schedules/recipients/request", "POST"),
        ("/api/v1/schedules/recipients/confirm", "POST"),
        ("/api/v1/schedules/drafts/{draft_id}", "GET"),
        ("/api/v1/schedules/drafts/{draft_id}/confirm", "POST"),
        ("/api/v1/schedules/drafts/{draft_id}/cancel", "POST"),
        ("/api/v1/schedules/{schedule_id}", "PATCH"),
        ("/api/v1/schedules/{schedule_id}", "DELETE"),
        ("/api/v1/schedules/{schedule_id}/run-now", "POST"),
        ("/api/v1/settings/chat", "GET"),
        ("/api/v1/settings/chat", "PUT"),
        # Intentional: the owner-only Notion auto-approve setting (authenticated, default off).
        ("/api/v1/settings/notion-auto-approve", "GET"),
        ("/api/v1/settings/notion-auto-approve", "PUT"),
        ("/api/v1/settings/connections", "GET"),
        ("/api/v1/settings/connections/{provider}", "PUT"),
        ("/api/v1/settings/connections/{provider}", "DELETE"),
        ("/api/v1/settings/connections/{provider}/test", "POST"),
        ("/api/v1/chat/sessions", "GET"),
        ("/api/v1/chat/sessions/{session_id}", "DELETE"),
        # Right-click menus (docs/api/CHAT.md): rename/pin/archive, branch, export, feedback.
        ("/api/v1/chat/sessions/{session_id}", "PATCH"),
        ("/api/v1/chat/sessions/{session_id}/duplicate", "POST"),
        ("/api/v1/chat/sessions/{session_id}/export", "GET"),
        ("/api/v1/chat/messages/{message_id}/feedback", "POST"),
        ("/api/v1/chat/sessions/{session_id}/messages", "GET"),
        ("/api/v1/chat/sessions/{session_id}/messages/{message_id}", "GET"),
        (
            "/api/v1/chat/sessions/{session_id}/messages/{message_id}/files/{file_id}",
            "GET",
        ),
        # History deletion (docs/api/CHAT.md, docs/api/TASKS_SPECIFICATION.md): added on purpose.
        (
            "/api/v1/chat/sessions/{session_id}/messages/{message_id}/files/{file_id}",
            "DELETE",
        ),
        ("/api/v1/tasks", "POST"),
        ("/api/v1/tasks", "GET"),
        ("/api/v1/tasks/delete", "POST"),
        ("/api/v1/tasks/{task_id}", "GET"),
        ("/api/v1/tasks/{task_id}", "DELETE"),
        ("/api/v1/tasks/{task_id}/abort", "POST"),
        ("/api/v1/tasks/{task_id}/events", "GET"),
        ("/api/v1/tasks/{task_id}/input", "POST"),
        ("/api/v1/approvals", "GET"),
        ("/api/v1/approvals/{request_id}", "GET"),
        ("/api/v1/approvals/{request_id}/decide", "POST"),
        ("/api/v1/mcp/servers", "GET"),
        ("/api/v1/mcp/servers", "POST"),
        ("/api/v1/mcp/servers/{server_id}/health", "GET"),
        ("/api/v1/mcp/servers/{server_id}", "DELETE"),
        ("/api/v1/mcp-client/status", "GET"),  # read-only MCP client status (ADR-0028)
        ("/api/v1/decisions/status", "GET"),  # read-only decision model status (ADR-0029)
        ("/api/v1/search/status", "GET"),  # read-only per-engine web search state (WEB_SEARCH.md)
        ("/api/v1/artifacts", "GET"),
        ("/api/v1/artifacts/delete", "POST"),
        ("/api/v1/artifacts/{artifact_id}", "GET"),
        ("/api/v1/artifacts/{artifact_id}", "DELETE"),
        ("/api/v1/artifacts/{artifact_id}/metadata", "GET"),
        ("/api/v1/audit", "GET"),
        ("/api/v1/agents", "GET"),
        ("/api/v1/agents/custom", "POST"),
        ("/api/v1/agents/custom/preview", "POST"),
        ("/api/v1/agents/custom/draft", "POST"),
        ("/api/v1/agents/custom/{name}", "PUT"),
        ("/api/v1/agents/custom/{name}/enabled", "POST"),
        ("/api/v1/files/folder", "GET"),
        ("/api/v1/files/folder", "PUT"),
        ("/api/v1/files/folder", "DELETE"),
        ("/api/v1/files/folder/tree", "GET"),
        ("/api/v1/files/uploads", "POST"),
        ("/api/v1/files/uploads", "GET"),
        ("/api/v1/files/uploads/{upload_id}", "GET"),
        ("/api/v1/files/uploads/{upload_id}", "DELETE"),
        ("/api/v1/files/uploads/{upload_id}/download", "GET"),
        ("/api/v1/browser/action", "POST"),
        ("/api/v1/browser/session/{session_id}/takeover", "GET"),
        ("/api/v1/browser/session/{session_id}/resume", "POST"),
        ("/api/v1/memory/items", "GET"),
        ("/api/v1/memory/items", "POST"),
        ("/api/v1/memory/items/{item_id}", "DELETE"),
        ("/api/v1/memory/purge", "DELETE"),
        ("/api/v1/memory/search", "POST"),
    }


def test_the_whole_account_lifecycle_over_http(api: Api) -> None:
    account = api.register()
    assert (
        api.post("token", username=account["username"], password=GOOD_PASSWORD).status_code == 401
    )

    code = api.harness.email.codes_for(account["email"])[-1]
    assert api.post(
        "verify-email", email=account["email"], code=code, password=GOOD_PASSWORD
    ).json() == {"status": "verified"}

    tokens = api.login(account)
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 900
    me = api.client.get(f"{BASE}/me", headers=bearer(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert set(me.json()) == {"user_id", "workspace_id", "role", "email", "display_name"}
    # The caller sees their own email and name (display only).
    assert me.json()["email"] == account["email"]
    assert me.json()["display_name"] == account["full_name"]

    rotated = api.post("refresh", refresh_token=tokens["refresh_token"])
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert api.post("refresh", refresh_token=tokens["refresh_token"]).status_code == 401  # replay

    # The replay revoked the session, so the rotated token is dead too.
    assert api.post("refresh", refresh_token=rotated.json()["refresh_token"]).status_code == 401

    again = api.login(account)
    assert api.post("logout", refresh_token=again["refresh_token"]).status_code == 204
    assert api.client.get(f"{BASE}/me", headers=bearer(again["access_token"])).status_code == 401


def test_cookie_transport_login_sets_an_http_only_refresh_cookie(api: Api) -> None:
    account = api.register_and_verify()
    response = api.client.post(
        f"{BASE}/token",
        json={"username": account["username"], "password": GOOD_PASSWORD},
        headers=COOKIE_TRANSPORT,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["token_type"] == "bearer"
    assert "refresh_token" not in response.json()
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    morsel = cookie["maya_refresh"]
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api/v1/auth"
    assert morsel["max-age"] == "604800"
    assert morsel["secure"] is True


def test_cookie_refresh_rotates_and_reuse_revokes_the_session(api: Api) -> None:
    account = api.register_and_verify()
    login = api.client.post(
        f"{BASE}/token",
        json={"username": account["username"], "password": GOOD_PASSWORD},
        headers=COOKIE_TRANSPORT,
    )
    old_cookie = refresh_cookie(login)

    rotated = api.client.post(
        f"{BASE}/refresh",
        headers={**COOKIE_TRANSPORT, **cookie_header(old_cookie)},
    )
    assert rotated.status_code == 200
    assert "refresh_token" not in rotated.json()
    new_cookie = refresh_cookie(rotated)
    assert new_cookie != old_cookie

    replay = api.client.post(
        f"{BASE}/refresh",
        headers={**COOKIE_TRANSPORT, **cookie_header(old_cookie)},
    )
    assert replay.status_code == 401
    assert replay.json() == {"error": "invalid_token"}
    assert "Max-Age=0" in replay.headers["set-cookie"]
    assert (
        api.client.post(
            f"{BASE}/refresh",
            headers={**COOKIE_TRANSPORT, **cookie_header(new_cookie)},
        ).status_code
        == 401
    )


def test_missing_cookie_fails_with_invalid_token_and_clears_cookie(api: Api) -> None:
    response = api.client.post(f"{BASE}/refresh", headers=COOKIE_TRANSPORT)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Path=/api/v1/auth" in response.headers["set-cookie"]


def test_cookie_logout_clears_and_revokes_the_session(api: Api) -> None:
    account = api.register_and_verify()
    login = api.client.post(
        f"{BASE}/token",
        json={"username": account["username"], "password": GOOD_PASSWORD},
        headers=COOKIE_TRANSPORT,
    )
    refresh_token = refresh_cookie(login)

    logout = api.client.post(
        f"{BASE}/logout",
        headers={**COOKIE_TRANSPORT, **cookie_header(refresh_token)},
    )
    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]
    failed_refresh = api.client.post(
        f"{BASE}/refresh",
        headers={**COOKIE_TRANSPORT, **cookie_header(refresh_token)},
    )
    assert failed_refresh.status_code == 401


def test_cookie_secure_flag_follows_the_setting(api_with_insecure_cookie: Api) -> None:
    account = api_with_insecure_cookie.register_and_verify()
    response = api_with_insecure_cookie.client.post(
        f"{BASE}/token",
        json={"username": account["username"], "password": GOOD_PASSWORD},
        headers=COOKIE_TRANSPORT,
    )

    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert cookie["maya_refresh"]["secure"] == ""


def test_legacy_refresh_and_logout_still_use_json_tokens(api: Api) -> None:
    account = api.register_and_verify()
    tokens = api.login(account)
    refreshed = api.post("refresh", refresh_token=tokens["refresh_token"])
    assert refreshed.status_code == 200
    assert "refresh_token" in refreshed.json()
    assert "set-cookie" not in refreshed.headers
    assert api.post("logout", refresh_token=refreshed.json()["refresh_token"]).status_code == 204


def test_password_recovery_over_http_ends_every_session(api: Api) -> None:
    account = api.register_and_verify()
    tokens = api.login(account)
    assert api.post("forgot-password", email=account["email"]).status_code == 202
    code = api.harness.email.codes_for(account["email"])[-1]
    reset = api.post("reset-password", email=account["email"], code=code, new_password=NEW_PASSWORD)
    assert reset.status_code == 200
    assert reset.json() == {"status": "password_reset"}
    assert api.client.get(f"{BASE}/me", headers=bearer(tokens["access_token"])).status_code == 401
    assert (
        api.post("token", username=account["username"], password=GOOD_PASSWORD).status_code == 401
    )
    assert api.post("token", username=account["username"], password=NEW_PASSWORD).status_code == 200


def _shape(response: Any) -> tuple[int, Any, dict[str, str]]:
    return (
        response.status_code,
        response.json(),
        {name: response.headers.get(name, "") for name in HEADERS_THAT_MUST_MATCH},
    )


def test_registration_answers_identically_for_every_outcome(api: Api) -> None:
    verified = api.register_and_verify()
    unverified = api.register()
    outcomes = {
        "new account": api.registration(),
        "verified e-mail": api.registration(email=verified["email"]),
        "verified username": api.registration(username=verified["username"]),
        "unverified e-mail": api.registration(email=unverified["email"]),
        "unverified username": api.registration(username=unverified["username"]),
        "e-mail in other case": api.registration(email=verified["email"].upper()),
    }
    shapes = {
        name: _shape(api.client.post(f"{BASE}/register", json=body))
        for name, body in outcomes.items()
    }
    assert set(map(repr, shapes.values())) == {repr(shapes["new account"])}, shapes
    assert shapes["new account"][0] == 202
    assert shapes["new account"][1] == {"status": "accepted"}


def test_password_recovery_requests_answer_identically_for_known_and_unknown_addresses(
    api: Api,
) -> None:
    known = api.register_and_verify()
    responses = [
        api.post("forgot-password", email=known["email"]),
        api.post("forgot-password", email="nobody.at.all@example.com"),
        api.post("resend-verification", email=known["email"]),
        api.post("resend-verification", email="nobody.at.all@example.com"),
    ]
    assert len({repr(_shape(response)) for response in responses}) == 1
    assert responses[0].status_code == 202


def test_every_code_failure_looks_the_same(api: Api) -> None:
    pending = api.register()
    verified = api.register_and_verify()
    cases = [
        api.post("verify-email", email=pending["email"], code="000000", password=GOOD_PASSWORD),
        api.post(
            "verify-email", email="nobody.at.all@example.com", code="000000", password=GOOD_PASSWORD
        ),
        api.post("verify-email", email=verified["email"], code="000000", password=GOOD_PASSWORD),
        api.post("verify-email", email=pending["email"], code="12ab", password=GOOD_PASSWORD),
        api.post(
            "verify-email",
            email=pending["email"],
            code=api.harness.email.codes_for(pending["email"])[-1],
            password="not the password!!",  # right code, wrong password
        ),
        api.post(
            "reset-password",
            email="nobody.at.all@example.com",
            code="000000",
            new_password=NEW_PASSWORD,
        ),
        api.post(
            "reset-password", email=verified["email"], code="000000", new_password=NEW_PASSWORD
        ),
    ]
    assert {repr(_shape(response)) for response in cases} == {
        repr((400, {"error": "invalid_or_expired_code"}, _shape(cases[0])[2]))
    }


def test_verifying_an_address_requires_the_password(api: Api) -> None:
    account = api.register()
    code = api.harness.email.codes_for(account["email"])[-1]
    missing = api.post("verify-email", email=account["email"], code=code)
    assert (missing.status_code, missing.json()) == (
        422,
        {"error": "validation_error", "fields": ["password"]},
    )
    with_password = api.post(
        "verify-email", email=account["email"], code=code, password=account["password"]
    )
    assert with_password.status_code == 200


def test_login_failures_do_not_reveal_which_part_was_wrong(api: Api) -> None:
    account = api.register_and_verify()
    wrong = api.post("token", username=account["username"], password="not the password!!")
    unknown = api.post("token", username="nobody_at_all", password="not the password!!")
    assert _shape(wrong) == _shape(unknown)
    assert wrong.status_code == 401
    assert wrong.json() == {"error": "invalid_credentials"}


def test_unverified_login_matches_unknown_and_wrong_password_responses(api: Api) -> None:
    verified = api.register_and_verify()
    unverified = api.register()
    responses = [
        api.post("token", username=verified["username"], password="not the password"),
        api.post("token", username="unknown_user", password=GOOD_PASSWORD),
        api.post("token", username=unverified["email"], password=GOOD_PASSWORD),
    ]
    assert len({repr(_shape(response)) for response in responses}) == 1
    assert responses[0].status_code == 401
    assert responses[0].json() == {"error": "invalid_credentials"}


def test_validation_errors_name_fields_but_never_echo_values(api: Api) -> None:
    secret = "Sentinel-Fake-Value-123"
    bodies = [
        {"username": "UPPER space", "email": "not-an-email", "password": secret, "full_name": "x"},
        {**api.registration(), "password": "x" * 300},
        {**api.registration(), "unexpected": secret},
        {"username": secret, "email": secret, "password": secret, "full_name": secret},
    ]
    for body in bodies:
        response = api.client.post(f"{BASE}/register", json=body)
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"
        assert secret not in response.text
        assert "x" * 300 not in response.text
    weak = api.client.post(f"{BASE}/register", json={**api.registration(), "password": "short"})
    assert weak.status_code == 422
    assert weak.json() == {"error": "password_too_short"}


def test_malformed_json_and_wrong_content_types_are_rejected_cleanly(api: Api) -> None:
    assert (
        api.client.post(
            f"{BASE}/token", content="{bad", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert (
        api.client.post(f"{BASE}/token", data={"username": "a", "password": "b"}).status_code == 422
    )


def test_unknown_routes_and_methods_use_the_same_error_shape(api: Api) -> None:
    missing = api.client.get("/api/v1/nope")
    assert (missing.status_code, missing.json()) == (404, {"error": "not_found"})
    wrong_method = api.client.get(f"{BASE}/token")
    assert (wrong_method.status_code, wrong_method.json()) == (405, {"error": "method_not_allowed"})


def test_rate_limits_answer_429_with_retry_after(api: Api) -> None:
    body = api.registration()
    for _ in range(5):
        api.client.post(f"{BASE}/register", json={**body, "username": api.harness.identity()[0]})
    blocked = api.client.post(
        f"{BASE}/register", json={**body, "username": api.harness.identity()[0]}
    )
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limited"}
    assert blocked.headers["Retry-After"] == "3600"


def test_a_failing_rate_limiter_answers_503_not_a_free_pass(api: Api) -> None:
    api.harness.limiter.unavailable = True
    response = api.client.post(f"{BASE}/register", json=api.registration())
    assert (response.status_code, response.json()) == (503, {"error": "service_unavailable"})


def test_without_working_e_mail_the_e_mail_endpoints_answer_503(api_without_email: Api) -> None:
    for path, body in (
        ("register", api_without_email.registration()),
        ("forgot-password", {"email": "someone@example.com"}),
        ("resend-verification", {"email": "someone@example.com"}),
    ):
        response = api_without_email.post(path, **body)
        assert (response.status_code, response.json()) == (503, {"error": "service_unavailable"})


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({}, "not_authenticated"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, "not_authenticated"),
        ({"Authorization": "Bearer"}, "not_authenticated"),
        ({"Authorization": "Bearer not.a.token"}, "invalid_token"),
    ],
)
def test_protected_endpoint_rejects_missing_and_malformed_credentials(
    api: Api, headers: dict[str, str], error: str
) -> None:
    response = api.client.get(f"{BASE}/me", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": error}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_expired_and_swapped_tokens_are_refused(api: Api) -> None:
    account = api.register_and_verify()
    tokens = api.login(account)
    assert api.client.get(f"{BASE}/me", headers=bearer(tokens["refresh_token"])).status_code == 401
    assert api.post("refresh", refresh_token=tokens["access_token"]).status_code == 401
    api.harness.clock.advance(minutes=15)
    assert api.client.get(f"{BASE}/me", headers=bearer(tokens["access_token"])).status_code == 401


def test_logout_with_an_invalid_token_is_unauthorized(api: Api) -> None:
    assert api.post("logout", refresh_token="garbage").status_code == 401


def test_responses_never_contain_secrets_or_hashes(api: Api) -> None:
    account = api.register_and_verify()
    tokens = api.login(account)
    me = api.client.get(f"{BASE}/me", headers=bearer(tokens["access_token"]))
    for response in (me, api.post("token", username=account["username"], password=GOOD_PASSWORD)):
        assert "argon2" not in response.text
        assert "hashed_password" not in response.text
        assert GOOD_PASSWORD not in response.text


def test_logs_never_contain_credentials_codes_tokens_or_addresses(api: Api) -> None:
    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = Capture()
    logging.getLogger("maya").addHandler(handler)
    try:
        account = api.register()
        code = api.harness.email.codes_for(account["email"])[-1]
        api.post("verify-email", email=account["email"], code=code, password=GOOD_PASSWORD)
        api.post("token", username=account["username"], password="a wrong password!!")
        tokens = api.login(account)
        api.post("forgot-password", email=account["email"])
        api.harness.clock.advance(minutes=2)
        api.post("forgot-password", email=account["email"])
        reset_code = api.harness.email.codes_for(account["email"])[-1]
        api.post(
            "reset-password", email=account["email"], code=reset_code, new_password=NEW_PASSWORD
        )
        api.post("refresh", refresh_token=tokens["refresh_token"])
        api.post("logout", refresh_token=tokens["refresh_token"])
    finally:
        logging.getLogger("maya").removeHandler(handler)
    log = "\n".join(captured)
    assert "auth_event" in log  # something was logged, so the check below means something
    for secret in (
        GOOD_PASSWORD,
        NEW_PASSWORD,
        "a wrong password!!",
        code,
        reset_code,
        tokens["access_token"],
        tokens["refresh_token"],
        account["email"],
        account["username"],
    ):
        assert secret not in log


def test_identify_is_neutral_and_does_not_query_account_storage(api: Api) -> None:
    account = api.register_and_verify()
    expected_body = {"status": "accepted"}

    with (
        patch.object(api.harness.service, "_find_by_email", new_callable=AsyncMock) as by_email,
        patch.object(
            api.harness.service, "_find_by_username", new_callable=AsyncMock
        ) as by_username,
    ):
        responses = [
            api.post("identify", identifier=account["email"]),
            api.post("identify", identifier="unknownuser@example.com"),
            api.post("identify", identifier=account["username"]),
            api.post("identify", identifier="unknown_username"),
        ]

    assert all(
        response.status_code == 200 and response.json() == expected_body for response in responses
    )
    by_email.assert_not_awaited()
    by_username.assert_not_awaited()


def test_identify_invalid_format_returns_422(api: Api) -> None:
    res = api.post("identify", identifier="   ")
    assert res.status_code == 422


def test_signup_otp_and_complete_flow(api: Api) -> None:
    email = "newbie@example.com"
    # Step 1: Send signup OTP
    res_otp = api.post("signup/otp", email=email)
    assert res_otp.status_code == 202

    codes = api.harness.email.codes_for(email)
    assert len(codes) == 1
    code = codes[0]

    # Step 2: Complete signup with OTP
    res_complete = api.post(
        "signup/complete",
        email=email,
        code=code,
        username="newbie_user",
        password=GOOD_PASSWORD,
        full_name="Newbie User",
    )
    assert res_complete.status_code == 200
    data = res_complete.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Step 3: Identify gives the same neutral response after account creation.
    ident = api.post("identify", identifier=email)
    assert ident.status_code == 200
    assert ident.json() == {"status": "accepted"}

    # Step 4: Login with username or email works immediately
    res_login_user = api.post("token", username="newbie_user", password=GOOD_PASSWORD)
    assert res_login_user.status_code == 200
    res_login_email = api.post("token", username=email, password=GOOD_PASSWORD)
    assert res_login_email.status_code == 200


def test_signup_complete_with_invalid_otp_returns_400(api: Api) -> None:
    email = "testwrongotp@example.com"
    api.post("signup/otp", email=email)
    res = api.post(
        "signup/complete",
        email=email,
        code="999999",
        username="wrong_user",
        password=GOOD_PASSWORD,
        full_name="Wrong User",
    )
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_or_expired_code"
