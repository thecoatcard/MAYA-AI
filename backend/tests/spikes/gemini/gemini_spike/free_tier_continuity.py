"""User-run, bounded Free-tier Live session continuity observation."""

import argparse
import asyncio
import getpass
import json
import secrets
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from tests.spikes.spike_s3_gemini_live import build_plans

from . import free_tier_smoke
from .adapters import (
    CLOSE_CODE_CATEGORIES,
    CLOSE_REASON_CATEGORIES,
    CLOSE_SECONDS,
    CONTINUITY_ACTIVITY_INTERVAL_SECONDS,
    CONTINUITY_MAX_AUDIO_BYTES,
    CONTINUITY_MAX_RESUMPTIONS,
    CONTINUITY_MAX_SECONDS,
    CONTINUITY_MAX_SYNTHETIC_TURNS,
    CONTINUITY_NONCE_WORDS,
    FAILURE_CATEGORIES,
    AdapterFailure,
    LiveObservation,
    observe_continuity_session,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_COUNT = 2
TOTAL_WALLTIME_SECONDS = SESSION_COUNT * (
    CONTINUITY_MAX_SECONDS
    + (CONTINUITY_MAX_RESUMPTIONS + 1) * CLOSE_SECONDS
    + 2 * free_tier_smoke.CLEANUP_SECONDS
)

SCOPE_TEXT = f"""\
Free-tier / no-linked-billing continuity observation only. The project state
cannot be independently verified by this launcher. No paid calls, billing
activation, retries, model fallback, tools, grounding, or user-provided content.

Workload: two separate text-input Live sessions with AUDIO responses and
output-audio transcription, one per input method;
each observes at most {CONTINUITY_MAX_SECONDS} seconds and uses at most
{CONTINUITY_MAX_RESUMPTIONS} resumptions. A built-in synthetic nonce is sent
once before reconnect and never replayed; after a resumed setup, the model is
asked to say the six synthetic words exactly. While no turn is outstanding, a
fixed nonce-free synthetic turn is sent every
{CONTINUITY_ACTIVITY_INTERVAL_SECONDS:g} seconds, at most
{CONTINUITY_MAX_SYNTHETIC_TURNS} extra turns per session; output audio is capped
at {CONTINUITY_MAX_AUDIO_BYTES // 48_000} seconds of PCM per session. Only SDK/model
identifiers, timestamps, numeric/boolean measurements, allowlisted
close/failure categories, and redacted status are saved. Audio and
transcription are inspected in memory and counted, never saved. Prompts,
responses, nonce, handles, API key, headers, and raw errors are never saved.

A finite timeout is not provider closure or proof of maximum lifetime. This
observation does not authorize paid usage. Any future paid use still requires
a separately reviewed enforceable hard spending cap and fresh owner approval.
"""


NonceFactory = Callable[[], str]
ClientFactory = free_tier_smoke.ClientFactory


def _new_nonce() -> str:
    return " ".join(secrets.choice(CONTINUITY_NONCE_WORDS) for _ in range(6))


def _allowlisted(value: str | None, allowed: frozenset[str]) -> str | None:
    """Persist only fixed labels; anything else becomes "unknown"."""
    if value is None:
        return None
    return value if value in allowed else "unknown"


def _session_evidence(observation: LiveObservation) -> dict[str, object]:
    return {
        "method": observation.method,
        "end_reason": observation.end_reason,
        "observed_seconds": observation.observed_seconds,
        "audio_bytes": observation.audio_bytes,
        "transcription_bytes": observation.transcription_bytes,
        "go_away_events": observation.go_away_events,
        "resumptions": observation.resumptions,
        "goaway_observed": observation.goaway_observed,
        "resumption_handle_observed": observation.resumption_handle_observed,
        "resumed_setup_observed": observation.resumed_setup_observed,
        "continuity_probe_completed": observation.continuity_probe_completed,
        "context_continuity_verified": observation.context_continuity_verified,
        "provider_closed": observation.provider_closed,
        "provider_close_code": observation.provider_close_code,
        "provider_close_code_category": _allowlisted(
            observation.provider_close_code_category, CLOSE_CODE_CATEGORIES
        ),
        "provider_close_reason_category": _allowlisted(
            observation.provider_close_reason_category, CLOSE_REASON_CATEGORIES
        ),
        "synthetic_turns_sent": observation.synthetic_turns_sent,
        "synthetic_turns_completed": observation.synthetic_turns_completed,
        "synthetic_turn_limit_reached": observation.synthetic_turn_limit_reached,
        "seconds_since_last_client_send": observation.seconds_since_last_client_send,
        "termination_observed": observation.termination_observed,
        "observation_limit_reached": observation.observation_limit_reached,
    }


def _new_evidence() -> dict[str, object]:
    return {
        "evidence_type": "user_run_free_tier_continuity_observation",
        "status": "failed",
        "live_acceptance_verified": False,
        "paid_calls_authorized": False,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "sdk": {"package": "google-genai", "version": version("google-genai")},
        "model": free_tier_smoke.MODEL_IDS["live"],
        "limits": {
            "independent_text_sessions": SESSION_COUNT,
            "per_session_observation_seconds": CONTINUITY_MAX_SECONDS,
            "max_resumptions_per_session": CONTINUITY_MAX_RESUMPTIONS,
            "synthetic_activity_interval_seconds": CONTINUITY_ACTIVITY_INTERVAL_SECONDS,
            "max_synthetic_turns_per_session": CONTINUITY_MAX_SYNTHETIC_TURNS,
            "max_output_audio_seconds_per_session": CONTINUITY_MAX_AUDIO_BYTES // 48_000,
            "total_walltime_seconds": TOTAL_WALLTIME_SECONDS,
            "automatic_retries": False,
            "http_transport_retries": False,
            "tools": False,
            "grounding": False,
            "model_fallback": False,
            "context_window_compression": False,
        },
        "account_scope": {
            "free_tier_confirmed_by_user": True,
            "no_linked_billing_confirmed_by_user": True,
            "programmatically_verified": False,
            "provider_cost_cap_enforced": False,
        },
        "observations": [],
        "limitations": [
            "Free-tier and no-linked-billing status are user attestations only.",
            "No paid use or billing activation is authorized.",
            (
                "Context continuity is verified only when the complete six-word nonce "
                "transcription matches after resumed setup."
            ),
            (
                "A local observation timeout is not provider closure or a maximum-lifetime "
                "measurement."
            ),
            (
                "No prompts, responses, nonce, handles, credentials, raw errors, raw close "
                "reasons, or user content are retained."
            ),
            (
                "Close code categories are RFC 6455 names for the measured code; reason "
                "categories match fixed known patterns only. Neither proves a root cause, "
                "and a 1008 policy-violation code alone does not show idleness."
            ),
        ],
    }


def _write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(evidence, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(encoded)


def _observation_is_complete(observation: LiveObservation) -> bool:
    return (
        observation.continuity_probe_completed
        and observation.context_continuity_verified
        and observation.goaway_observed
        and observation.resumption_handle_observed
        and observation.resumed_setup_observed
        and observation.end_reason in ("provider_closed", "observation_timeout")
    )


async def run_continuity_observation(
    api_key: str,
    output_path: Path,
    *,
    client_factory: ClientFactory = free_tier_smoke.create_sdk_client,
    nonce_factory: NonceFactory = _new_nonce,
) -> tuple[bool, dict[str, object]]:
    """Run only the two bounded text sessions and persist redacted evidence."""
    evidence = _new_evidence()
    observations = evidence["observations"]
    assert isinstance(observations, list)
    started = time.monotonic()
    current_stage = "session_1"
    try:
        async with asyncio.timeout(TOTAL_WALLTIME_SECONDS):
            for index, plan in enumerate(build_plans(), start=1):
                current_stage = f"session_{index}"
                client = client_factory(api_key)
                try:
                    observation = await observe_continuity_session(
                        client.aio.live,
                        plan,
                        nonce=nonce_factory(),
                    )
                finally:
                    await free_tier_smoke._close_client(client)
                observations.append(_session_evidence(observation))
                if not _observation_is_complete(observation):
                    code = (
                        "provider_disconnect"
                        if observation.end_reason == "provider_disconnected"
                        else "continuity_or_termination_not_observed"
                    )
                    evidence["failure"] = {
                        "stage": current_stage,
                        "code": code,
                    }
                    break
            else:
                evidence["status"] = "completed"
    except asyncio.CancelledError:
        evidence["failure"] = {"stage": current_stage, "code": "cancelled"}
        evidence["finished_at_utc"] = datetime.now(UTC).isoformat()
        evidence["total_elapsed_seconds"] = max(0.0, time.monotonic() - started)
        try:
            _write_evidence(output_path, evidence)
        except Exception:
            print(
                "Warning: cancellation evidence could not be saved.",
                file=sys.stderr,
            )
        raise
    except TimeoutError:
        evidence["failure"] = {"stage": current_stage, "code": "total_deadline_exceeded"}
    except AdapterFailure as error:
        evidence["failure"] = {
            "stage": current_stage,
            "code": "operation_failed",
            "category": _allowlisted(error.category, FAILURE_CATEGORIES),
        }
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


def _confirm_and_get_key() -> str | None:
    print(SCOPE_TEXT)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Refusing to run: an interactive terminal is required.", file=sys.stderr)
        return None
    if free_tier_smoke._has_forbidden_environment_override():
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


def _output_path(output_dir: str | None) -> Path:
    directory = Path(output_dir) if output_dir else PROJECT_ROOT / "results"
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return directory / f"gemini-free-tier-continuity-{timestamp}.json"


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
        completed, evidence = asyncio.run(run_continuity_observation(key, output_path))
    except Exception:
        print("Continuity observation failed; details were redacted.", file=sys.stderr)
        return 1
    finally:
        key = ""
    if completed:
        print(
            "Bounded continuity observation completed; this is not lifetime acceptance. "
            f"Redacted evidence saved to {output_path}"
        )
        return 0
    failure = evidence.get("failure")
    stage: object = "unknown"
    if isinstance(failure, dict):
        candidate = failure.get("stage", "unknown")
        if isinstance(candidate, str):
            stage = candidate
    print(
        f"Continuity observation failed at {stage}; redacted evidence saved to {output_path}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
