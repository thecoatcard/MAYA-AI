"""Single model policy for every agent and task model call.

All agent and task code asks this module which model alias to use, so one setting
(``MAYA_AGENT_MODEL_ALIAS``, default ``lite``) moves every agent at once. The chat path has its
own alias and does not use this module. ``live`` is refused at settings validation: it is a
stateful audio WebSocket API (docs/architecture/MODEL_GATEWAY.md section 10).

Each call also carries an ``affinity`` (the agent role, or ``chapter:{i}``) so the key pool keeps
each role on its own API key and spreads parallel chapters over the pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.config import Settings

DEFAULT_AGENT_ALIAS = "lite"
DEFAULT_SEARCH_ALIAS = "search"
# Per-attempt cap for agent calls (MAYA_AGENT_REQUEST_TIMEOUT_SECONDS).
DEFAULT_AGENT_TIMEOUT_SECONDS = 150.0

# Output cap for one long-form structured call (a chapter or an outline). 65 536 is the registry
# output limit of every agent-capable model; it is a ceiling, not a target (billing is per token
# actually produced), and it prevents a long chapter from being cut off by a smaller default.
LONG_OUTPUT_TOKENS = 65_536


@dataclass(frozen=True)
class AgentModelPolicy:
    alias: str = DEFAULT_AGENT_ALIAS
    fallback_alias: str | None = None
    timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS
    # Grounded search: the dedicated alias first, then this fallback (None = no fallback).
    search_alias: str = DEFAULT_SEARCH_ALIAS
    search_fallback_alias: str | None = None


_policy = AgentModelPolicy()


def configure(settings: Settings) -> AgentModelPolicy:
    """Install the policy from validated settings (called once at application start)."""
    global _policy
    fallback = (settings.agent_fallback_alias or "").strip() or None
    alias = settings.agent_model_alias
    search_alias = settings.agent_search_alias
    raw_search_fallback = settings.agent_search_fallback_alias
    # Unset (None) and empty both mean NO grounded fallback. The agent alias (gemini-3.5-flash-lite)
    # must not be the implicit fallback: its grounding quota was exhausted for the whole key pool
    # (live run, 429 scope gemini-3.5-flash-lite/grounding), so the fallback only added a second
    # doomed call. An explicit value is still honoured.
    search_fallback = raw_search_fallback or None
    _policy = AgentModelPolicy(
        alias,
        fallback if fallback != alias else None,
        float(settings.agent_request_timeout_seconds),
        search_alias,
        search_fallback if search_fallback != search_alias else None,
    )
    return _policy


def reset() -> None:
    """Restore the defaults (lite, no fallback). Used by tests."""
    global _policy
    _policy = AgentModelPolicy()


def current_policy() -> AgentModelPolicy:
    return _policy


def agent_alias() -> str:
    """The model alias every agent and task call uses."""
    return _policy.alias


def agent_call_kwargs(affinity: str | None = None) -> dict[str, Any]:
    """Gateway keyword arguments for an agent call: alias, fallback policy and key affinity."""
    kwargs: dict[str, Any] = {
        "alias": _policy.alias,
        "allow_fallback": _policy.fallback_alias is not None,
        "fallback_alias": _policy.fallback_alias,
        "attempt_timeout_seconds": _policy.timeout_seconds,
    }
    if affinity:
        kwargs["affinity"] = affinity
    return kwargs


def search_call_kwargs(affinity: str | None = None) -> dict[str, Any]:
    """Gateway keyword arguments for a grounded search call.

    The search alias is tried first, then the search fallback alias (none unless configured);
    when both are exhausted the gateway raises and the caller reports
    ``search_unavailable``.
    """
    kwargs: dict[str, Any] = {
        "alias": _policy.search_alias,
        "allow_fallback": _policy.search_fallback_alias is not None,
        "fallback_alias": _policy.search_fallback_alias,
        "attempt_timeout_seconds": _policy.timeout_seconds,
    }
    if affinity:
        kwargs["affinity"] = affinity
    return kwargs


def alternate_search_call_kwargs(affinity: str | None = None) -> dict[str, Any]:
    """Grounded-search kwargs on a DIFFERENT model alias than ``search_call_kwargs`` uses.

    Used for the one retry of a research step that got no usable answer: the first alias that is
    neither the search alias nor its fallback, with no further fallback of its own. The ``lite``
    alias (gemini-3.5-flash-lite) is never chosen here: its grounding quota is the one that was
    exhausted, so retrying on it only repeats the 429 (the gateway also skips keys that are in
    their quota cooldown for that model, so nothing spins inside the window).
    """
    used = {_policy.search_alias, _policy.search_fallback_alias}
    alias = next((a for a in ("orchestrator", "heavy") if a not in used), "orchestrator")
    kwargs: dict[str, Any] = {
        "alias": alias,
        "allow_fallback": False,
        "fallback_alias": None,
        "attempt_timeout_seconds": _policy.timeout_seconds,
    }
    if affinity:
        kwargs["affinity"] = affinity
    return kwargs


def custom_agent_alias(requested: str | None) -> str:
    """The alias a custom agent runs on: its own ``model`` when allowed, else the agent alias.

    Only the REST text aliases an agent may use are honoured (never ``live``); anything else
    silently falls back to the policy alias, so a hand-edited file cannot pick another model.
    """
    from app.gateway.registry import AGENT_ALIAS_CHOICES

    return requested if requested in AGENT_ALIAS_CHOICES else _policy.alias
