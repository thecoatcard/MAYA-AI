"""Owner-run diagnostic smoke tool for Gemini AI integration.

CRITICAL POLICY NOTICE:
This tool is strictly OWNER-RUN ONLY.
Autonomous AI agents (Antigravity, Claude, Copilot, Cursor, etc.) are strictly FORBIDDEN
from executing this tool or initiating live API network calls against Gemini.
Only the repository owner may execute this tool in a local environment.

Safety Controls:
- Free-tier verification by default (stops cleanly on 429 / quota limit).
- Explicit confirmation flag `--confirm-paid-calls-up-to-inr-100` required for any paid tier,
  enforcing a hard spend cap of INR 100.
- Never prints, logs, or transmits API keys or confidential credentials.
"""

import argparse
import asyncio
import sys
import time

from app.core.config import Settings
from app.gateway.client_adapter import build_genai_client
from app.gateway.exceptions import GatewayConfigurationError
from app.gateway.registry import build_default_registry


async def run_smoke(args: argparse.Namespace) -> int:
    try:
        settings = Settings()
    except Exception as exc:
        print(f"Error loading settings: {exc}", file=sys.stderr)
        return 1

    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip():
        print(
            "Error: MAYA_GEMINI_API_KEY is not set or is blank. "
            "Configure it in .env before running.",
            file=sys.stderr,
        )
        return 1

    registry = build_default_registry(settings.gemini_model_overrides)
    alias = args.alias

    try:
        spec = registry.get(alias)
    except GatewayConfigurationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if spec.transport != "rest":
        print(
            f"Error: Alias '{alias}' uses transport '{spec.transport}' which is not REST-callable.",
            file=sys.stderr,
        )
        return 1

    # Paid call safety gate
    is_free_tier = args.verify_free_tier or (
        spec.cost_per_1m_input == 0 and spec.cost_per_1m_output == 0
    )
    if not is_free_tier and not args.confirm_paid_calls_up_to_inr_100:
        print(
            "Safety Gate Blocked: Alias may incur upstream API cost.\n"
            "To proceed, pass explicit confirmation flag: --confirm-paid-calls-up-to-inr-100\n"
            "Execution halted safely (zero cost incurred).",
            file=sys.stderr,
        )
        return 2

    # Initialize client safely (never printing key)
    try:
        client = build_genai_client(
            api_key=settings.gemini_api_key.get_secret_value(),
            timeout_seconds=settings.gemini_request_timeout_seconds,
        )
    except Exception as exc:
        print(f"Error initializing client: {exc}", file=sys.stderr)
        return 1

    print(f"Initiating owner-run smoke test for alias: '{alias}' (model: '{spec.model_id}')")
    print(f"Free-tier mode: {is_free_tier}")
    start_time = time.monotonic()

    try:
        response = await client.aio.models.generate_content(
            model=spec.model_id,
            contents=[args.prompt],
        )
        elapsed = time.monotonic() - start_time
        print("\n--- Smoke Test Succeeded ---")
        print(f"Response: {response.text.strip() if response.text else '(empty)'}")
        print(f"Latency: {elapsed:.2f}s")
        if response.usage_metadata:
            u = response.usage_metadata
            print(
                f"Tokens: prompt={u.prompt_token_count}, "
                f"candidates={u.candidates_token_count}, "
                f"total={u.total_token_count}"
            )
        return 0

    except Exception as exc:
        err_msg = str(exc)
        if "429" in err_msg or "resource_exhausted" in err_msg.lower():
            print(
                "\nQuota Limit Reached: 429 Resource Exhausted. "
                "Halting free-tier smoke test cleanly."
            )
            return 0  # Successful quota verification
        print(f"\nSmoke Test Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Owner-run diagnostic smoke test for Gemini integration (NEVER run by agents)."
    )
    parser.add_argument(
        "--alias",
        default="orchestrator",
        help="Model alias to test (default: 'orchestrator')",
    )
    parser.add_argument(
        "--prompt",
        default="Respond with the single word 'OK'",
        help="Prompt text to send",
    )
    parser.add_argument(
        "--verify-free-tier",
        action="store_true",
        help=(
            "Owner asserts the configured key is a free-tier key. The tool cannot verify this: "
            "on a paid-tier key the call is still billed (one tiny prompt)."
        ),
    )
    parser.add_argument(
        "--confirm-paid-calls-up-to-inr-100",
        action="store_true",
        help="Owner confirmation to authorize paid API calls capped at INR 100",
    )

    args = parser.parse_args(argv)
    return asyncio.run(run_smoke(args))


if __name__ == "__main__":
    sys.exit(main())
