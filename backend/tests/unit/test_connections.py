"""Per-user connections: encryption, normalisation, error mapping, isolation, API, no leaks."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import capabilities
from app.api.v1.chat import get_chat_rate_limiter
from app.api.v1.connections import build_connection_service, router
from app.api.v1.errors import install_error_handlers
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.connections.crypto import (
    ConnectionCipher,
    ConnectionsNotConfigured,
    CredentialDecryptError,
    parse_key,
)
from app.connections.ownership import DeploymentOwnership
from app.connections.providers import (
    ConnectionInputError,
    Credentials,
    check_github,
    check_notion,
    normalize_notion_database_id,
    normalize_token,
)
from app.connections.service import ConnectionService, EnvDefaults
from app.memory.scrubber import PiiScrubber

KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
GH = "ghp_" + "a" * 36
NOTION = "ntn_" + "b" * 44
DB = "0123456789abcdef0123456789abcdef"


class MemStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], str] = {}

    async def get_text(self, ws: UUID, user: UUID, key: str) -> str | None:
        return self.rows.get((ws, user, key))

    async def set_text(self, ws: UUID, user: UUID, key: str, value: str) -> None:
        self.rows[(ws, user, key)] = value

    async def remove_key(self, ws: UUID, user: UUID, key: str) -> None:
        self.rows.pop((ws, user, key), None)


def http_returning(routes: dict[str, httpx.Response | Exception]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, answer in routes.items():
            if request.url.path.endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return httpx.Response(500)

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def service(
    store: MemStore | None = None,
    http: Any = None,
    env: EnvDefaults | None = None,
    key: bool = True,
    owner: UUID | None = None,
) -> ConnectionService:
    kwargs: dict[str, Any] = {} if http is None else {"http": http}
    if owner is not None:
        kwargs["deployment_owner"] = DeploymentOwnership(None, {str(owner)})
    return ConnectionService(
        store or MemStore(), ConnectionCipher.from_setting(KEY) if key else None, env, **kwargs
    )


# ------------------------------------------------------------------------------------ crypto


def test_round_trip_and_fresh_nonce() -> None:
    cipher = ConnectionCipher(parse_key(KEY))
    aad = ConnectionCipher.aad("w", "u", "github")
    a, b = cipher.encrypt("secret", aad), cipher.encrypt("secret", aad)
    assert a != b and "secret" not in a
    assert cipher.decrypt(a, aad) == "secret"


def test_wrong_owner_wrong_key_and_tamper_fail_closed() -> None:
    cipher = ConnectionCipher(parse_key(KEY))
    sealed = cipher.encrypt("secret", ConnectionCipher.aad("w", "alice", "github"))
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(sealed, ConnectionCipher.aad("w", "bob", "github"))
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(sealed, ConnectionCipher.aad("w", "alice", "notion"))
    other = ConnectionCipher(os.urandom(32))
    with pytest.raises(CredentialDecryptError):
        other.decrypt(sealed, ConnectionCipher.aad("w", "alice", "github"))
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(sealed[:-3] + "AAA", ConnectionCipher.aad("w", "alice", "github"))
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt("garbage", b"")


def test_bad_keys_are_rejected_without_echoing_them() -> None:
    with pytest.raises(ValueError) as err:
        parse_key("short-secret-value")
    assert "short-secret-value" not in str(err.value)
    assert ConnectionCipher.from_setting(None) is None
    assert ConnectionCipher.from_setting("  ") is None


# ------------------------------------------------------------------------------ normalisation


@pytest.mark.parametrize(
    "raw",
    [
        DB,
        "01234567-89ab-cdef-0123-456789abcdef",
        f"https://www.notion.so/My-Tasks-{DB}?v=ffffffffffffffffffffffffffffffff",
        f"https://www.notion.so/workspace/{DB}?v=1",
        f"https://team.notion.site/Tasks-{DB}",
    ],
)
def test_notion_database_id_forms_normalise(raw: str) -> None:
    assert normalize_notion_database_id(raw) == DB


@pytest.mark.parametrize("raw", ["", "abc", "https://evil.example/" + DB, "not an id at all"])
def test_notion_database_id_garbage_is_rejected(raw: str) -> None:
    with pytest.raises(ConnectionInputError):
        normalize_notion_database_id(raw)


def test_token_normalisation() -> None:
    assert normalize_token(f"  Bearer {GH}\n", label="GitHub") == GH
    for bad in ("", "short", "has space inside the token value xx", "tökén" + "x" * 30):
        with pytest.raises(ConnectionInputError):
            normalize_token(bad, label="GitHub")


# ------------------------------------------------------------------------------ error mapping


async def test_github_ok_shows_login_and_scopes() -> None:
    resp = httpx.Response(200, json={"login": "octo"}, headers={"x-oauth-scopes": "repo, read:org"})
    result = await check_github(Credentials(GH), http_returning({"/user": resp}))
    assert result.ok and result.account == "octo" and result.scopes == ("repo", "read:org")
    assert "octo" in result.message and GH not in result.message


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "token_rejected"),
        (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), "rate_limited"),
        (httpx.Response(403), "token_forbidden"),
        (httpx.Response(429, headers={"retry-after": "30"}), "rate_limited"),
        (httpx.Response(502), "provider_error"),
        (httpx.ConnectError("boom " + GH), "network"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
async def test_github_failures_have_precise_reasons(
    response: httpx.Response | Exception, code: str
) -> None:
    result = await check_github(Credentials(GH), http_returning({"/user": response}))
    assert not result.ok and result.code == code and GH not in result.message


def notion(db_answer: httpx.Response | Exception, me: httpx.Response | None = None) -> Any:
    return http_returning(
        {
            "/users/me": me or httpx.Response(200, json={"name": "Maya bot"}),
            f"/databases/{DB}": db_answer,
        }
    )


async def test_notion_ok_reads_the_database_title() -> None:
    db = httpx.Response(200, json={"title": [{"plain_text": "Tasks"}]})
    result = await check_notion(Credentials(NOTION, DB), notion(db))
    assert result.ok and result.database_title == "Tasks"


@pytest.mark.parametrize(
    ("db", "code", "word"),
    [
        (httpx.Response(404, json={"code": "object_not_found"}), "database_not_shared", "Share"),
        (httpx.Response(400, json={"code": "validation_error"}), "database_id_invalid", "page"),
        (httpx.Response(401), "token_rejected", "rejected"),
        (httpx.Response(403), "token_forbidden", "permission"),
        (httpx.Response(429), "rate_limited", "rate"),
        (httpx.ConnectError("x"), "network", "network"),
    ],
)
async def test_notion_database_failures(db: Any, code: str, word: str) -> None:
    result = await check_notion(Credentials(NOTION, DB), notion(db))
    assert not result.ok and result.code == code and word.lower() in result.message.lower()


async def test_notion_token_rejected_and_bad_id_and_no_id() -> None:
    bad = await check_notion(
        Credentials(NOTION, DB), notion(httpx.Response(200), me=httpx.Response(401))
    )
    assert bad.code == "token_rejected"
    fmt = await check_notion(Credentials(NOTION, "nope"), notion(httpx.Response(200)))
    assert fmt.code == "database_id_format"
    none = await check_notion(Credentials(NOTION, None), notion(httpx.Response(200)))
    assert none.ok and none.code == "ok_no_database"


# ----------------------------------------------------------------------- service and isolation


async def test_save_encrypts_and_status_has_no_secret() -> None:
    store = MemStore()
    svc = service(store)
    ws, user = uuid4(), uuid4()
    status = await svc.save(ws, user, "github", token=GH, database_id=None)
    raw = next(iter(store.rows.values()))
    assert GH not in raw and GH not in repr(status) and status.state == "saved"
    assert GH not in repr(await svc.resolve(ws, user, "github"))  # token excluded from repr


async def test_users_never_resolve_each_others_credentials() -> None:
    store = MemStore()
    svc = service(store)
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await svc.save(ws, alice, "github", token=GH, database_id=None)
    assert (await svc.resolve(ws, alice, "github")).token == GH  # type: ignore[union-attr]
    assert await svc.resolve(ws, bob, "github") is None
    # Even if a row were copied to Bob's key, the owner-bound ciphertext does not open for him.
    (key,) = list(store.rows)
    store.rows[(ws, bob, key[2])] = store.rows[key]
    assert await svc.resolve(ws, bob, "github") is None
    assert (await svc.status_of(ws, bob, "github")).state == "unreadable"


async def test_environment_default_is_the_fallback_and_user_wins() -> None:
    env = EnvDefaults(github_token="ghp_" + "e" * 36, notion_token=NOTION, notion_database_id=DB)
    # Deliberate change: the environment fallback now applies ONLY to the deployment owner
    # (MAYA_ENV_CONNECTIONS_USERS); everyone else: test_server_connections_owner.py.
    ws, user = uuid4(), uuid4()
    svc = service(env=env, owner=user)
    got = await svc.resolve(ws, user, "github")
    assert got is not None and got.source == "environment"
    assert (await svc.status_of(ws, user, "github")).state == "server_default"
    await svc.save(ws, user, "github", token=GH, database_id=None)
    got = await svc.resolve(ws, user, "github")
    assert got is not None and got.source == "user" and got.token == GH


async def test_no_key_fails_closed_but_env_default_still_works() -> None:
    owner = uuid4()
    svc = service(key=False, env=EnvDefaults(github_token=GH), owner=owner)
    with pytest.raises(ConnectionsNotConfigured):
        await svc.save(uuid4(), uuid4(), "github", token=GH, database_id=None)
    assert await svc.resolve(uuid4(), owner, "github") is not None


async def test_notion_database_can_change_without_resending_the_token() -> None:
    svc = service()
    ws, user = uuid4(), uuid4()
    await svc.save(ws, user, "notion", token=NOTION, database_id=DB)
    other = "f" * 32
    status = await svc.save(ws, user, "notion", token=None, database_id=other)
    assert status.database_id == other
    got = await svc.resolve(ws, user, "notion")
    assert got is not None and got.token == NOTION
    with pytest.raises(ConnectionInputError):
        await svc.save(ws, user, "notion", token=None, database_id="garbage")


async def test_test_result_is_remembered_and_disconnect_removes() -> None:
    http = http_returning({"/user": httpx.Response(200, json={"login": "octo"})})
    svc = service(http=http)
    ws, user = uuid4(), uuid4()
    assert (await svc.test(ws, user, "github")).code == "token_missing"
    await svc.save(ws, user, "github", token=GH, database_id=None)
    assert (await svc.test(ws, user, "github")).ok
    status = await svc.status_of(ws, user, "github")
    assert status.state == "connected" and status.account == "octo"
    await svc.disconnect(ws, user, "github")
    assert (await svc.status_of(ws, user, "github")).state == "not_connected"


async def test_capabilities_are_decided_per_user() -> None:
    svc = service()
    ws, alice, bob = uuid4(), uuid4(), uuid4()
    await svc.save(ws, alice, "github", token=GH, database_id=None)

    async def resolver(w: UUID, u: UUID) -> tuple[bool, bool]:
        return await svc.is_connected(w, u, "github"), await svc.is_connected(w, u, "notion")

    capabilities.set_user_connections(resolver)
    try:
        assert (await capabilities.for_user(ws, alice)).github is True
        assert (await capabilities.for_user(ws, bob)).github is False
        with capabilities.bound(await capabilities.for_user(ws, alice)):
            assert capabilities.precheck("list my repos") is None
        with capabilities.bound(await capabilities.for_user(ws, bob)):
            refusal = capabilities.precheck("list my repos")
            assert refusal is not None and "Settings > Connections" in refusal.hint
    finally:
        capabilities.reset()


# ---------------------------------------------------------------------------------------- API


class AllowAll:
    async def hit(self, key: str, limit: Any) -> bool:
        return True

    async def release(self, key: str, limit: Any) -> None:
        return None


def make_client(svc: ConnectionService, principal: Principal) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[build_connection_service] = lambda: svc
    app.dependency_overrides[get_chat_rate_limiter] = lambda: AllowAll()
    return TestClient(app)


def principal(ws: UUID, user: UUID) -> Principal:
    return Principal(user_id=user, workspace_id=ws, role="member", session_id=uuid4())


def test_api_round_trip_never_returns_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    http = http_returning({"/user": httpx.Response(200, json={"login": "octo"})})
    svc = service(http=http)
    ws, user = uuid4(), uuid4()
    client = make_client(svc, principal(ws, user))
    caplog.set_level(logging.DEBUG)
    put = client.put("/api/v1/settings/connections/github", json={"token": GH})
    assert put.status_code == 200
    body = put.json()
    assert body["test"]["ok"] and body["connection"]["state"] == "connected"
    listing = client.get("/api/v1/settings/connections")
    assert listing.json()["encryption_available"] is True
    assert put.headers["cache-control"] == "no-store"
    test = client.post("/api/v1/settings/connections/github/test")
    gone = client.delete("/api/v1/settings/connections/github")
    for response in (put, listing, test, gone):
        assert GH not in response.text
    assert GH not in caplog.text
    assert gone.json()["connection"]["state"] == "not_connected"


def test_api_errors_have_reasons_and_do_not_echo_input() -> None:
    client = make_client(service(), principal(uuid4(), uuid4()))
    short = client.put("/api/v1/settings/connections/github", json={"token": "short"})
    assert short.status_code == 422 and short.json()["error"] == "token_format"
    assert client.put("/api/v1/settings/connections/nope", json={"token": GH}).status_code == 404
    extra = client.put("/api/v1/settings/connections/github", json={"token": GH, "x": 1})
    assert extra.status_code == 422 and GH not in extra.text
    too_long = client.put("/api/v1/settings/connections/github", json={"token": "z" * 700})
    assert too_long.status_code == 422 and "zzzz" not in too_long.text
    no_key = make_client(service(key=False), principal(uuid4(), uuid4()))
    answer = no_key.put("/api/v1/settings/connections/github", json={"token": GH})
    assert answer.status_code == 503 and answer.json()["error"] == "connections_not_configured"


def test_api_users_are_isolated() -> None:
    store = MemStore()
    http = http_returning({"/user": httpx.Response(200, json={"login": "octo"})})
    ws = uuid4()
    alice = make_client(service(store, http), principal(ws, uuid4()))
    bob = make_client(service(store, http), principal(ws, uuid4()))
    alice.put("/api/v1/settings/connections/github", json={"token": GH})
    states = {
        c["provider"]: c["state"]
        for c in bob.get("/api/v1/settings/connections").json()["connections"]
    }
    assert states["github"] == "not_connected"
    assert bob.post("/api/v1/settings/connections/github/test").json()["test"]["code"] == (
        "token_missing"
    )


def test_api_rate_limit() -> None:
    class Deny:
        async def hit(self, key: str, limit: Any) -> bool:
            return False

    svc = service()
    client = make_client(svc, principal(uuid4(), uuid4()))
    client.app.dependency_overrides[get_chat_rate_limiter] = lambda: Deny()  # type: ignore[attr-defined]
    assert client.get("/api/v1/settings/connections").status_code == 429


# ---------------------------------------------------------------------------------- scrubbing


def test_scrubber_removes_notion_and_fine_grained_github_tokens() -> None:
    text = f"a {NOTION} b github_pat_{'c' * 40} c secret_{'d' * 43}"
    out = PiiScrubber().scrub(text).clean_text
    assert NOTION not in out and "github_pat_" not in out and "secret_d" not in out
    assert json.dumps(out).count("REDACTED_SECRET") == 3
