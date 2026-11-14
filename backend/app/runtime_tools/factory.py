"""Build the runtime-tool service from settings. ``None`` when the feature is off."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.runtime_tools.executor import ContainerExecutor, ToolExecutor
from app.runtime_tools.service import RuntimeToolService


def build_runtime_tools(
    settings: Settings,
    *,
    storage_root: Path | str,
    executor: ToolExecutor | None = None,
) -> RuntimeToolService | None:
    """The kill switch lives here: ``MAYA_RUNTIME_TOOLS_ENABLED`` false (the default) builds
    nothing, so the tools are not offered to the model and no code path can run. The executor is
    the container runner unless a test passes a fake; with no sandbox image it reports
    unavailable and every run is refused."""
    if not settings.runtime_tools_enabled:
        return None
    return RuntimeToolService(
        storage_root=storage_root,
        executor=executor or ContainerExecutor(settings.runtime_tools_sandbox_image),
        timeout_seconds=settings.runtime_tools_timeout_seconds,
        max_runs_per_turn=settings.runtime_tools_max_runs_per_turn,
    )


__all__ = ["build_runtime_tools"]
