from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest

from app.core.security_tokens import (
    ACCESS_AUDIENCE,
    CLOCK_SKEW_SECONDS,
    ISSUER,
    REFRESH_AUDIENCE,
    TokenCodec,
    TokenError,
)
from tests.support.clock import ManualClock

KEY = b"j" * 32
USER, WORKSPACE, SESSION, JTI = uuid4(), uuid4(), uuid4(), uuid4()
ACCESS_TTL = timedelta(minutes=15)


def build(clock: ManualClock | None = None, key: bytes = KEY) -> tuple[TokenCodec, ManualClock]:
    manual = clock or ManualClock()
    return TokenCodec(key, manual, ACCESS_TTL), manual


def access_token(codec: TokenCodec) -> str:
    token, _ = codec.issue_access(
        user_id=USER, workspace_id=WORKSPACE, role="admin", session_id=SESSION, token_id=JTI
    )
    return token


def refresh_token(
    codec: TokenCodec, clock: ManualClock, lifetime: timedelta, generation: int = 0
) -> str:
    return codec.issue_refresh(
        user_id=USER,
        workspace_id=WORKSPACE,
        session_id=SESSION,
        token_id=JTI,
        generation=generation,
        expires_at=clock.now() + lifetime,
    )


def forged(clock: ManualClock, **changes: Any) -> dict[str, Any]:
    now = int(clock.now().timestamp())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": ACCESS_AUDIENCE,
        "sub": str(USER),
        "jti": str(JTI),
        "typ": "access",
        "iat": now,
        "exp": now + 900,
        "workspace_id": str(WORKSPACE),
        "role": "admin",
        "sid": str(SESSION),
    }
    claims.update(changes)
    return {key: value for key, value in claims.items() if value is not None}


def test_access_token_round_trip_carries_the_expected_claims() -> None:
    codec, clock = build()
    token, expires_at = codec.issue_access(
        user_id=USER, workspace_id=WORKSPACE, role="user", session_id=SESSION, token_id=JTI
    )
    claims = codec.decode_access(token)
    assert (claims.user_id, claims.workspace_id, claims.session_id) == (USER, WORKSPACE, SESSION)
    assert (claims.role, claims.token_id) == ("user", JTI)
    assert claims.expires_at == expires_at == clock.now() + ACCESS_TTL
    raw = jwt.decode(token, options={"verify_signature": False})
    assert set(raw) == {
        "iss",
        "aud",
        "sub",
        "jti",
        "typ",
        "iat",
        "exp",
        "workspace_id",
        "role",
        "sid",
    }
    assert raw["typ"] == "access"


def test_refresh_token_round_trip() -> None:
    codec, clock = build()
    token = refresh_token(codec, clock, timedelta(days=7))
    claims = codec.decode_refresh(token)
    assert (claims.user_id, claims.workspace_id, claims.session_id) == (USER, WORKSPACE, SESSION)
    assert claims.token_id == JTI
    assert claims.expires_at == clock.now() + timedelta(days=7)
    assert "role" not in jwt.decode(token, options={"verify_signature": False})


@pytest.mark.parametrize("generation", [0, 1, 7])
def test_refresh_token_carries_its_generation(generation: int) -> None:
    codec, clock = build()
    token = refresh_token(codec, clock, timedelta(days=1), generation)
    assert codec.decode_refresh(token).generation == generation


@pytest.mark.parametrize("value", [None, -1, "1", 1.5, True])
def test_a_refresh_token_without_a_valid_generation_is_rejected(value: object) -> None:
    codec, clock = build()
    claims = forged(clock, aud=REFRESH_AUDIENCE, typ="refresh", gen=value)
    claims.pop("role")
    with pytest.raises(TokenError):
        codec.decode_refresh(jwt.encode(claims, KEY, algorithm="HS256"))


def test_access_token_expires_exactly_at_its_lifetime() -> None:
    codec, clock = build()
    token = access_token(codec)
    clock.advance(seconds=ACCESS_TTL.total_seconds() - 1)
    codec.decode_access(token)
    clock.advance(seconds=1)
    with pytest.raises(TokenError):
        codec.decode_access(token)


def test_expired_refresh_token_is_rejected_unless_explicitly_allowed() -> None:
    codec, clock = build()
    token = refresh_token(codec, clock, timedelta(hours=1))
    clock.advance(hours=1)
    with pytest.raises(TokenError):
        codec.decode_refresh(token)
    assert codec.decode_refresh(token, allow_expired=True).session_id == SESSION


def test_tokens_are_not_interchangeable() -> None:
    codec, clock = build()
    with pytest.raises(TokenError):
        codec.decode_refresh(access_token(codec))
    with pytest.raises(TokenError):
        codec.decode_access(refresh_token(codec, clock, timedelta(days=1)))


def test_a_token_with_the_wrong_type_claim_is_rejected_even_with_the_right_audience() -> None:
    codec, clock = build()
    token = jwt.encode(forged(clock, typ="refresh"), KEY, algorithm="HS256")
    with pytest.raises(TokenError):
        codec.decode_access(token)


def test_tampered_signature_and_wrong_key_are_rejected() -> None:
    codec, _ = build()
    token = access_token(codec)
    header, payload, signature = token.split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    for bad in (f"{header}.{payload}.{flipped}", f"{header}.{payload}.", f"{header}.{payload}"):
        with pytest.raises(TokenError):
            codec.decode_access(bad)
    other, _ = build(key=b"z" * 32)
    with pytest.raises(TokenError):
        other.decode_access(token)


def test_payload_swap_with_the_original_signature_is_rejected() -> None:
    codec, clock = build()
    good = access_token(codec)
    other = jwt.encode(forged(clock, role="admin", sub=str(uuid4())), KEY, algorithm="HS256")
    mixed = ".".join([good.split(".")[0], other.split(".")[1], good.split(".")[2]])
    with pytest.raises(TokenError):
        codec.decode_access(mixed)


@pytest.mark.filterwarnings("ignore:The HMAC key is")
def test_algorithm_downgrade_and_none_are_rejected() -> None:
    codec, clock = build()
    claims = forged(clock)
    with pytest.raises(TokenError):
        codec.decode_access(jwt.encode(claims, KEY, algorithm="HS512"))
    with pytest.raises(TokenError):
        codec.decode_access(jwt.encode(claims, None, algorithm="none"))


@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "jti", "exp", "iat", "typ"])
def test_missing_required_claims_are_rejected(claim: str) -> None:
    codec, clock = build()
    token = jwt.encode(forged(clock, **{claim: None}), KEY, algorithm="HS256")
    with pytest.raises(TokenError):
        codec.decode_access(token)


@pytest.mark.parametrize("claim", ["workspace_id", "role", "sid"])
def test_missing_access_specific_claims_are_rejected(claim: str) -> None:
    codec, clock = build()
    token = jwt.encode(forged(clock, **{claim: None}), KEY, algorithm="HS256")
    with pytest.raises(TokenError):
        codec.decode_access(token)


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "someone-else"},
        {"aud": REFRESH_AUDIENCE},
        {"sub": "not-a-uuid"},
        {"workspace_id": "not-a-uuid"},
        {"sid": 5},
        {"role": ""},
        {"role": 7},
        {"exp": "soon"},
        {"exp": True},
        {"nbf": 1},
    ],
)
def test_malformed_claim_values_are_rejected(changes: dict[str, Any]) -> None:
    codec, clock = build()
    token = jwt.encode(forged(clock, **changes), KEY, algorithm="HS256")
    with pytest.raises(TokenError):
        codec.decode_access(token)


def test_future_dated_tokens_are_rejected_beyond_the_skew_allowance() -> None:
    codec, clock = build()
    now = int(clock.now().timestamp())
    ok = jwt.encode(forged(clock, iat=now + CLOCK_SKEW_SECONDS), KEY, algorithm="HS256")
    codec.decode_access(ok)
    bad = jwt.encode(forged(clock, iat=now + CLOCK_SKEW_SECONDS + 1), KEY, algorithm="HS256")
    with pytest.raises(TokenError):
        codec.decode_access(bad)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "....", "eyJ.eyJ.sig"])
def test_garbage_is_rejected_without_detail(garbage: str) -> None:
    codec, _ = build()
    with pytest.raises(TokenError, match="^Invalid token$"):
        codec.decode_access(garbage)
    with pytest.raises(TokenError):
        codec.decode_refresh(garbage)
