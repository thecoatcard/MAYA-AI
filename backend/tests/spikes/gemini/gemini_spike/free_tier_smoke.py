"""User-run, explicitly confirmed free-tier-only S3/S4 smoke launcher."""

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import httpx
from google import genai
from google.genai import types
from google.genai.client import Client, DebugConfig

from .adapters import (
    EmbeddingClient,
    EmbeddingObservation,
    LiveClient,
    LiveObservation,
    embed_synthetic,
    observe_smoke_pair,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEVELOPER_API_URL = "https://generativelanguage.googleapis.com"
DEVELOPER_API_VERSION = "v1beta"
MODEL_IDS = {
    "embeddings": "gemini-embedding-2",
    "live": "gemini-3.8-live",
}
PER_EMBEDDING_TIMEOUT_SECONDS = 30
EMBEDDING_TOTAL_TIMEOUT_SECONDS = 60
LIVE_SESSION_TIMEOUT_SECONDS = 60
MAX_LIVE_AUDIO_SECONDS = 10
TOTAL_WALLTIME_SECONDS = 300
OPERATION_DEADLINE_SECONDS = 285
CLEANUP_SECONDS = 2

# These are explicit SDK, transport, and auth variables inspected in
# google-genai 2.34.0 / HTTPX 0.28.1. Values are never read or printed.
FORBIDDEN_ENVIRONMENT_OVERRIDES = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_VERTEX_BASE_URL",
    "GOOGLE_GENAI_CLIENT_MODE",
    "GOOGLE_GENAI_REPLAYS_DIRECTORY",
    "GOOGLE_GENAI_REPLAY_ID",
    "GOOGLE_GENAI_HTTP_CLIENT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "WS_PROXY",
    "WSS_PROXY",
    "SOCKS_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "ws_proxy",
    "wss_proxy",
    "socks_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)

SCOPE_TEXT = """\
Free-tier-only smoke. This launcher does not enforce provider billing or verify
account tier; your Free-tier / no-linked-billing confirmations are your attestation.
Paid calls, billing activation, fallback models, retries and reconnects are prohibited.

Workload: three separate synthetic embedding requests (<=30 seconds each);
two separate synthetic text-to-audio sessions (<=60 seconds each, one completed
audio turn, <=10 seconds of returned audio); <=5 minutes total. No tools or
grounding. The candidate Live model may be unavailable. Only redacted measurements
and status are saved; prompts, audio, API keys, handles, raw headers and exceptions
are not included. A successful smoke does not verify lifetime, resumption, or cost.
"""


class AsyncSdkClient(EmbeddingClient, LiveClient, Protocol):
    """The SDK's async client surface used by both isolated adapters."""


class SdkClient(Protocol):
    @property
    def aio(self) -> AsyncSdkClient: ...

    def close(self) -> None: ...


ClientFactory = Callable[[str], SdkClient]


class SmokeFailure(RuntimeError):
    """Fixed, redacted failure; never includes SDK details or credentials."""


def create_sdk_client(api_key: str) -> Client:
    """Construct an explicit Developer API client, never ADC/Vertex or ambient routing."""
    return genai.Client(
        api_key=api_key,
        enterprise=False,
        vertexai=False,
        debug_config=DebugConfig(
            client_mode=None,
            replays_directory=None,
            replay_id=None,
        ),
        http_options=types.HttpOptions(
            base_url=DEVELOPER_API_URL,
            api_version=DEVELOPER_API_VERSION,
            timeout=LIVE_SESSION_TIMEOUT_SECONDS * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
            client_args={
                "trust_env": False,
                "transport": httpx.HTTPTransport(retries=0),
            },
            async_client_args={
                "trust_env": False,
                "transport": httpx.AsyncHTTPTransport(retries=0),
            },
        ),
    )


async def _close_client(client: SdkClient) -> None:
    """Close SDK async and sync transports on success, failure, and cancellation."""

    async def close_both() -> None:
        failed = False
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                await client.aio.aclose()
        except Exception:
            failed = True
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                await asyncio.to_thread(client.close)
        except Exception:
            failed = True
        if failed:
            raise SmokeFailure("client_cleanup_failed")

    cleanup_task = asyncio.create_task(close_both())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        try:
            await cleanup_task
        finally:
            raise


async def _run_embeddings(api_key: str, client_factory: ClientFactory) -> EmbeddingObservation:
    client = client_factory(api_key)
    try:
        return await embed_synthetic(
            client.aio,
            max_seconds=EMBEDDING_TOTAL_TIMEOUT_SECONDS,
            request_timeout_seconds=PER_EMBEDDING_TIMEOUT_SECONDS,
            close_client=False,
        )
    finally:
        await _close_client(client)


async def _run_live_sessions(api_key: str, client_factory: ClientFactory) -> list[LiveObservation]:
    client = client_factory(api_key)
    try:
        return await observe_smoke_pair(
            client.aio,
            close_client=False,
        )
    finally:
        await _close_client(client)


def _embedding_evidence(observation: EmbeddingObservation) -> dict[str, object]:
    return {
        "request_count": 3,
        "per_request_timeout_seconds": PER_EMBEDDING_TIMEOUT_SECONDS,
        "metrics": asdict(observation.metrics),
        "billable_character_count": observation.billable_character_count,
    }


def _live_evidence(observations: list[LiveObservation]) -> list[dict[str, object]]:
    return [
        {
            "method": item.method,
            "end_reason": item.end_reason,
            "first_audio_latency_ms": item.first_audio_latency_ms,
            "audio_duration_ms": item.audio_duration_ms,
            "audio_bytes": item.audio_bytes,
            "turn_completions": item.turn_completions,
            "observed_seconds": item.observed_seconds,
        }
        for item in observations
    ]


def _new_evidence() -> dict[str, object]:
    return {
        "evidence_type": "user_run_live_measurement",
        "status": "failed",
        "live_acceptance_verified": False,
        "paid_calls_authorized": False,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "sdk": {"package": "google-genai", "version": version("google-genai")},
        "models": dict(MODEL_IDS),
        "limits": {
            "embedding_requests": 3,
            "embedding_request_timeout_seconds": PER_EMBEDDING_TIMEOUT_SECONDS,
            "live_sessions": 2,
            "live_session_timeout_seconds": LIVE_SESSION_TIMEOUT_SECONDS,
            "max_live_audio_seconds": MAX_LIVE_AUDIO_SECONDS,
            "total_walltime_seconds": TOTAL_WALLTIME_SECONDS,
            "automatic_retries": False,
            "http_transport_retries": False,
            "reconnections": False,
            "session_resumption": False,
            "tools": False,
            "grounding": False,
            "model_fallback": False,
        },
        "account_scope": {
            "free_tier_confirmed_by_user": True,
            "no_linked_billing_confirmed_by_user": True,
            "programmatically_verified": False,
            "provider_cost_cap_enforced": False,
        },
        "observations": {},
        "limitations": [
            "The user confirmations are attestations, not independent account verification.",
            "No provider-side cost cap is enforced; no paid or fallback call is authorized.",
            "The smoke does not verify session lifetime or resumption/context continuity.",
            "Model availability and access are not guaranteed; failures stop without fallback.",
            "Only built-in synthetic text and audio are sent; no content is retained.",
        ],
    }


def _write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(evidence, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(encoded)


async def run_smoke(
    api_key: str,
    output_path: Path,
    *,
    client_factory: ClientFactory = create_sdk_client,
) -> tuple[bool, dict[str, object]]:
    """Run only the approved sequence and persist redacted evidence."""
    evidence = _new_evidence()
    observations = evidence["observations"]
    assert isinstance(observations, dict)
    started = time.monotonic()
    current_stage = "s4_embeddings"
    try:
        async with asyncio.timeout(OPERATION_DEADLINE_SECONDS):
            embeddings = await _run_embeddings(api_key, client_factory)
            observations["s4_embeddings"] = _embedding_evidence(embeddings)
            current_stage = "s3_live_sessions"
            live = await _run_live_sessions(api_key, client_factory)
            observations["s3_live_sessions"] = _live_evidence(live)
        evidence["status"] = "completed"
    except asyncio.CancelledError:
        evidence["failure"] = {"stage": current_stage, "code": "cancelled"}
        evidence["finished_at_utc"] = datetime.now(UTC).isoformat()
        evidence["total_elapsed_seconds"] = max(0.0, time.monotonic() - started)
        try:
            _write_evidence(output_path, evidence)
        except Exception:
            pass
        raise
    except TimeoutError:
        evidence["failure"] = {"stage": current_stage, "code": "total_deadline_exceeded"}
    except Exception:
        evidence["failure"] = {"stage": current_stage, "code": "operation_failed"}
    evidence["finished_at_utc"] = datetime.now(UTC).isoformat()
    elapsed = max(0.0, time.monotonic() - started)
    evidence["total_elapsed_seconds"] = elapsed
    if elapsed > TOTAL_WALLTIME_SECONDS:
        evidence["status"] = "failed"
        evidence["failure"] = {"stage": "evidence_persistence", "code": "walltime_exceeded"}
    _write_evidence(output_path, evidence)
    return evidence["status"] == "completed", evidence


def _has_forbidden_environment_override() -> bool:
    return any(name in os.environ for name in FORBIDDEN_ENVIRONMENT_OVERRIDES)


def _output_path(output_dir: str | None) -> Path:
    directory = Path(output_dir) if output_dir else PROJECT_ROOT / "results"
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return directory / f"gemini-free-tier-smoke-{timestamp}.json"


def _confirm_and_get_key() -> str | None:
    print(SCOPE_TEXT)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Refusing to run: an interactive terminal is required.", file=sys.stderr)
        return None
    if _has_forbidden_environment_override():
        print(
            "Refusing to run: an SDK/auth/transport environment override is present.",
            file=sys.stderr,
        )
        return None
    if input("Type YES to confirm this key is for your Free-tier project: ") != "YES":
        print("Free-tier confirmation not provided; no credential requested.", file=sys.stderr)
        return None
    if input("Type YES to confirm that no billing account is linked: ") != "YES":
        print(
            "No-linked-billing confirmation not provided; no credential requested.",
            file=sys.stderr,
        )
        return None
    key = getpass.getpass("Gemini Developer API key (hidden input): ")
    if not key.strip() or key != key.strip():
        print("Refusing to run: the hidden credential was blank or malformed.", file=sys.stderr)
        return None
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        help="Directory for redacted JSON evidence (default: isolated project results/).",
    )
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Refusing to run: an interactive terminal is required.", file=sys.stderr)
        return 2
    key = _confirm_and_get_key()
    if key is None:
        return 2
    try:
        output_path = _output_path(args.output_dir)
        completed, evidence = asyncio.run(run_smoke(key, output_path))
    except Exception:
        print("Smoke failed; details were redacted.", file=sys.stderr)
        return 1
    finally:
        key = ""
    if completed:
        print(f"Smoke completed; redacted evidence saved to {output_path}")
        return 0
    failure = evidence.get("failure")
    stage: object = "unknown"
    if isinstance(failure, dict):
        candidate = failure.get("stage", "unknown")
        if isinstance(candidate, str):
            stage = candidate
    print(
        f"Smoke failed at {stage}; redacted evidence saved to {output_path}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
