"""Model Gateway startup verification and remote model discovery."""

import logging
from typing import Any

from app.core.config import Settings
from app.gateway.exceptions import GatewayStartupError
from app.gateway.registry import ModelRegistry, normalize_model_id

logger = logging.getLogger("maya")


async def verify_gateway_at_startup(
    registry: ModelRegistry,
    settings: Settings,
    client: Any = None,
) -> None:
    """Verify Model Gateway integrity at application startup.

    1. Offline Validation (always runs): validates registry fallback chains and integrity.
    2. Remote Model Discovery (optional):
       - If settings.gemini_verify_models_on_startup is False: logs info and skips.
       - If settings.gemini_verify_models_on_startup is True:
         - If settings.gemini_api_key is None or blank: logs warning and skips gracefully.
         - If client is None: logs warning and skips gracefully.
         - Otherwise: lists available models from provider and asserts all enabled
           MVP aliases with verify_at_startup=True are present in provider list.

    Raises:
        GatewayStartupError: If a required model is missing or discovery query fails.
    """
    # Step 1: Always validate static configuration integrity
    registry.validate_configuration()

    # Step 2: Config-gated remote verification
    if not settings.gemini_verify_models_on_startup:
        logger.info("gateway_startup_remote_check_disabled")
        return

    if settings.gemini_api_key is None:
        logger.warning("gateway_startup_remote_check_skipped_no_api_key")
        return

    key_value = settings.gemini_api_key.get_secret_value()
    if not key_value.strip():
        logger.warning("gateway_startup_remote_check_skipped_no_api_key")
        return

    if client is None:
        logger.warning("gateway_startup_remote_check_skipped_no_client")
        return

    try:
        model_pager = await client.aio.models.list()
        available_model_ids: set[str] = set()

        # Handle either async iterator or list/page response
        if hasattr(model_pager, "__aiter__"):
            async for m in model_pager:
                name = getattr(m, "name", "")
                available_model_ids.add(normalize_model_id(name))
        else:
            for m in model_pager:
                name = getattr(m, "name", "")
                available_model_ids.add(normalize_model_id(name))

        # Assert required enabled models are present
        for alias, spec in registry.specs.items():
            if spec.enabled and spec.verify_at_startup:
                if spec.model_id not in available_model_ids:
                    raise GatewayStartupError(
                        f"Startup model verification failed: required model '{spec.model_id}' "
                        f"for alias '{alias}' was not found in provider model list."
                    )

        logger.info("gateway_startup_remote_models_verified")
    except GatewayStartupError:
        raise
    except Exception as exc:
        # Only the exception class is reported: provider error text is never logged.
        logger.error("gateway_startup_remote_check_failed error=%s", type(exc).__name__)
        raise GatewayStartupError(f"Startup model discovery failed ({type(exc).__name__})") from exc
