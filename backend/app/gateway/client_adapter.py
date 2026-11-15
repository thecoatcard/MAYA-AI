"""Google GenAI SDK client adapter and offline factory.

Note:
    Live network operations against the Gemini API are NEVER executed by the automated
    test harness or agent without explicit owner authorization and budget caps.
    This adapter is offline-verified via mock contracts and unit tests.
    Status: NOT TESTED LIVE (offline verification only).
"""

from typing import TYPE_CHECKING

from google import genai
from google.genai import types

from app.gateway.exceptions import GatewayConfigurationError

if TYPE_CHECKING:
    from app.core.config import Settings


def build_genai_client(
    api_key: str | None,
    timeout_seconds: float = 30.0,
) -> genai.Client:
    """Build a Google GenAI Client configured with explicit API key and timeout.

    The explicit ``api_key`` takes precedence over GEMINI_API_KEY and GOOGLE_API_KEY, and
    ``vertexai=False`` pins the Gemini Developer API so that an ambient
    GOOGLE_GENAI_USE_VERTEXAI=true cannot switch the client to Vertex AI mode.

    SDK-level retries are disabled with ``HttpRetryOptions(attempts=1)`` ("0 or 1 means no
    retries"). ``retry_options=None`` would NOT disable them: the SDK then applies its own
    implicit retry on transport errors. All retry, backoff and fallback policy belongs to
    ``ResiliencePolicy`` so that every attempt is metered and bounded.

    Raises:
        GatewayConfigurationError: If api_key is None or empty.
    """
    if not api_key or not api_key.strip():
        raise GatewayConfigurationError(
            "Cannot construct Gemini client: MAYA_GEMINI_API_KEY is not configured."
        )

    # SDK timeout is specified in milliseconds
    timeout_ms = int(timeout_seconds * 1000)

    return genai.Client(
        api_key=api_key.strip(),
        vertexai=False,
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def create_client_from_settings(settings: "Settings") -> genai.Client | None:
    """Construct client from application Settings if key is set, else return None."""
    effective_keys = getattr(settings, "effective_gemini_api_keys", [])
    if effective_keys:
        key_str = effective_keys[0].get_secret_value()
    elif settings.gemini_api_key is not None:
        key_str = settings.gemini_api_key.get_secret_value()
    else:
        return None

    if not key_str.strip():
        return None
    return build_genai_client(
        api_key=key_str,
        timeout_seconds=settings.gemini_request_timeout_seconds,
    )
