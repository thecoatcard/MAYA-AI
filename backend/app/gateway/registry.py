"""Model registry defining role aliases, pricing, limits, and fallback traversal."""

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from app.gateway.exceptions import GatewayConfigurationError, UnsupportedAliasTransportError

TransportType = Literal["rest", "websocket", "managed_agent", "decision", "unsupported"]
MODEL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][-a-zA-Z0-9.]{1,63}$")
ALLOWED_OVERRIDE_ALIASES = frozenset(
    {"orchestrator", "heavy", "lite", "embed", "embed-fallback", "search"}
)
# Text-generation REST aliases an agent call may use (MAYA_AGENT_MODEL_ALIAS / _FALLBACK_ALIAS).
AGENT_ALIAS_CHOICES = frozenset({"lite", "orchestrator", "heavy"})
# Aliases a grounded (Google Search) call may use: the agent aliases plus the dedicated ``search``
# alias, whose model has a grounding quota of its own (docs/architecture/MODEL_GATEWAY.md).
SEARCH_ALIAS_CHOICES = AGENT_ALIAS_CHOICES | {"search"}
_LIVE_ALIASES = frozenset({"live", "live-thinking"})
# Last-resort text model: plain generation chains (lite, orchestrator, heavy) end here when every
# model before it is overloaded or out of quota. It is the ``search`` alias (gemini-2.5-flash), so
# its model id follows GEMINI_MODEL_OVERRIDES; its price is unknown and accounted as such.
LAST_RESORT_ALIAS = "search"


def validate_agent_alias(
    value: str, setting: str, choices: frozenset[str] = AGENT_ALIAS_CHOICES
) -> str:
    """Return ``value`` if an agent may use it, else raise ValueError with a clear reason."""
    if value in _LIVE_ALIASES:
        raise ValueError(
            f"{setting} cannot be '{value}': live is a stateful audio WebSocket API, "
            "use it for voice only"
        )
    if value not in choices:
        raise ValueError(f"{setting} must be an enabled REST text alias: {sorted(choices)}")
    return value


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    model_id: str
    fallback_alias: str | None
    context_input_limit: int
    context_output_limit: int | None
    cost_per_1m_input: Decimal | None
    cost_per_1m_output: Decimal | None
    cost_per_1m_cached: Decimal | None
    transport: TransportType = "rest"
    verify_at_startup: bool = True
    is_post_mvp: bool = False
    enabled: bool = True
    price_unknown: bool = False
    context_unverified: bool = False

    def check_supported_transport(self) -> None:
        """Raise UnsupportedAliasTransportError if transport is not callable via Gateway REST."""
        if self.transport != "rest":
            raise UnsupportedAliasTransportError(
                f"Alias '{self.alias}' uses transport '{self.transport}' which is registry-only "
                "and not supported for direct gateway execution in this runtime."
            )


def normalize_model_id(name: str) -> str:
    """Normalize model names returned by provider API (strip 'models/' prefix)."""
    return name.removeprefix("models/")


class ModelRegistry:
    """Registry of configured model aliases with terminating fallback resolution."""

    def __init__(self, specs: dict[str, ModelSpec]) -> None:
        self._specs = dict(specs)
        # Aliases whose model startup verification could not find. Fallback traversal skips them.
        self._unavailable: set[str] = set()

    def mark_unavailable(self, alias: str) -> None:
        """Record that ``alias``' model is missing at the provider; fallbacks skip it."""
        if alias in self._specs:
            self._unavailable.add(alias)

    def is_unavailable(self, alias: str) -> bool:
        return alias in self._unavailable

    @property
    def specs(self) -> dict[str, ModelSpec]:
        return dict(self._specs)

    def get(self, alias: str) -> ModelSpec:
        try:
            return self._specs[alias]
        except KeyError:
            raise GatewayConfigurationError(f"Unknown model alias: '{alias}'") from None

    def get_fallback(self, alias: str) -> ModelSpec | None:
        spec = self.get(alias)
        if spec.fallback_alias is None:
            return None
        return self.get(spec.fallback_alias)

    def resolve_fallback_chain(self, start_alias: str) -> list[str]:
        """Resolve the terminating sequence of aliases starting from start_alias."""
        visited: set[str] = set()
        chain: list[str] = []
        current: str | None = start_alias

        while current is not None:
            if current in visited:
                # Cycle detected; traversal terminates here
                break
            visited.add(current)
            chain.append(current)
            spec = self.get(current)
            current = spec.fallback_alias

        return chain

    def candidate_chain(
        self,
        start_alias: str,
        *,
        allow_fallback: bool = True,
        fallback_alias: str | None = None,
        last_resort: bool = False,
    ) -> list[str]:
        """The ordered aliases a call may try: no duplicates (cycle safe), unavailable skipped.

        ``fallback_alias`` replaces the registry chain with that single alias. ``last_resort``
        appends the last-resort text model (``LAST_RESORT_ALIAS``) when the call starts on a
        text-generation alias and the last-resort alias is an enabled REST model.
        """
        if not allow_fallback:
            return [start_alias]
        if fallback_alias is not None:
            chain = (
                [start_alias] if fallback_alias == start_alias else [start_alias, fallback_alias]
            )
        else:
            chain = self.resolve_fallback_chain(start_alias)
        # Skip aliases startup verification found missing (never the alias the caller asked for).
        chain = [a for i, a in enumerate(chain) if i == 0 or a not in self._unavailable] or chain
        if last_resort and start_alias in AGENT_ALIAS_CHOICES and LAST_RESORT_ALIAS not in chain:
            spec = self._specs.get(LAST_RESORT_ALIAS)
            if (
                spec is not None
                and spec.enabled
                and spec.transport == "rest"
                and LAST_RESORT_ALIAS not in self._unavailable
                # A last resort that is the same model as one already in the chain adds nothing.
                and all(self._specs[a].model_id != spec.model_id for a in chain)
            ):
                chain.append(LAST_RESORT_ALIAS)
        return chain

    def validate_configuration(self) -> None:
        """Validate registry integrity: enabled aliases must resolve terminating fallbacks."""
        for alias, spec in self._specs.items():
            if not spec.enabled:
                continue

            if spec.fallback_alias is not None:
                if spec.fallback_alias not in self._specs:
                    raise GatewayConfigurationError(
                        f"Alias '{alias}' references unknown fallback alias '{spec.fallback_alias}'"
                    )
                target = self._specs[spec.fallback_alias]
                if not target.enabled:
                    raise GatewayConfigurationError(
                        f"Enabled alias '{alias}' references disabled fallback "
                        f"alias '{spec.fallback_alias}'"
                    )

            # Ensure resolution terminates cleanly
            chain = self.resolve_fallback_chain(alias)
            if not chain:
                raise GatewayConfigurationError(f"Alias '{alias}' has empty fallback chain")

    def with_overrides(self, overrides: dict[str, str]) -> "ModelRegistry":
        """Return a new ModelRegistry with model_id overrides applied to REST aliases."""
        if not overrides:
            return self

        updated_specs = dict(self._specs)
        for alias, new_model_id in overrides.items():
            if alias not in updated_specs:
                raise GatewayConfigurationError(f"Cannot override unknown alias '{alias}'")

            spec = updated_specs[alias]
            if spec.transport != "rest" or not spec.enabled:
                raise GatewayConfigurationError(
                    f"Cannot override non-REST or disabled alias '{alias}'"
                )

            if not MODEL_ID_PATTERN.fullmatch(new_model_id):
                raise GatewayConfigurationError(
                    f"Invalid model_id '{new_model_id}' for alias '{alias}'"
                )

            # Pointing an alias at a model the registry already prices (for example the
            # orchestrator at the lite model) keeps that model's rates; any other id has no
            # known price.
            priced = next(
                (
                    known
                    for known in self._specs.values()
                    if known.model_id == new_model_id
                    and known.transport == "rest"
                    and not known.price_unknown
                ),
                None,
            )
            if priced is not None:
                updated_specs[alias] = replace(
                    spec,
                    model_id=new_model_id,
                    cost_per_1m_input=priced.cost_per_1m_input,
                    cost_per_1m_output=priced.cost_per_1m_output,
                    cost_per_1m_cached=priced.cost_per_1m_cached,
                    price_unknown=False,
                )
            else:
                updated_specs[alias] = replace(spec, model_id=new_model_id, price_unknown=True)

        new_registry = ModelRegistry(updated_specs)
        new_registry._unavailable = set(self._unavailable)
        new_registry.validate_configuration()
        return new_registry


def build_default_registry(overrides: dict[str, str] | None = None) -> ModelRegistry:
    """Build the canonical MVP model registry per MAYA AI Final Plan §7.1 and owner decisions."""
    specs: dict[str, ModelSpec] = {
        # Core MVP HTTPS models
        "orchestrator": ModelSpec(
            alias="orchestrator",
            model_id="gemini-3.5-flash-lite",
            fallback_alias="lite",
            context_input_limit=1_048_576,
            context_output_limit=65_536,
            cost_per_1m_input=Decimal("0.75"),
            cost_per_1m_output=Decimal("3.75"),
            cost_per_1m_cached=Decimal("0.075"),
            transport="rest",
            verify_at_startup=True,
            enabled=True,
        ),
        "heavy": ModelSpec(
            alias="heavy",
            model_id="gemini-4-hydrogen",
            fallback_alias="orchestrator",
            context_input_limit=1_048_576,
            context_output_limit=65_536,
            cost_per_1m_input=Decimal("2.00"),
            cost_per_1m_output=Decimal("10.00"),
            cost_per_1m_cached=Decimal("0.10"),
            transport="rest",
            verify_at_startup=True,
            enabled=True,
        ),
        "lite": ModelSpec(
            alias="lite",
            model_id="gemini-3.1-flash-lite",
            fallback_alias="orchestrator",
            context_input_limit=1_048_576,
            context_output_limit=65_536,
            cost_per_1m_input=Decimal("0.30"),
            cost_per_1m_output=Decimal("2.50"),
            cost_per_1m_cached=Decimal("0.03"),
            transport="rest",
            verify_at_startup=True,
            enabled=True,
        ),
        # Grounded web search. Gemini 3.x grounding was quota-blocked for the whole key pool while
        # gemini-2.5-flash grounding worked, so grounded calls use their own alias and model.
        # The model id is not priced in this registry: do not invent a price.
        "search": ModelSpec(
            alias="search",
            model_id="gemini-3.5-flash-lite",
            fallback_alias=None,
            context_input_limit=1_048_576,
            context_output_limit=65_536,
            cost_per_1m_input=None,
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="rest",
            verify_at_startup=False,
            enabled=True,
            price_unknown=True,
            context_unverified=True,
        ),
        "embed": ModelSpec(
            alias="embed",
            model_id="gemini-embedding-2",
            fallback_alias="embed-fallback",
            context_input_limit=8_192,
            context_output_limit=None,
            cost_per_1m_input=Decimal("0.20"),
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="rest",
            verify_at_startup=True,
            enabled=True,
        ),
        # Secondary embedding fallback alias (maps model gemini-embedding-001; not in plan 7.1)
        "embed-fallback": ModelSpec(
            alias="embed-fallback",
            model_id="gemini-embedding-001",
            fallback_alias=None,
            context_input_limit=2_048,
            context_output_limit=None,
            cost_per_1m_input=None,
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="rest",
            verify_at_startup=False,
            enabled=True,
            price_unknown=True,
            context_unverified=True,
        ),
        # Registry-only models (unsupported for direct REST Gateway execution)
        "live": ModelSpec(
            alias="live",
            model_id="gemini-3.8-live",
            fallback_alias="live-thinking",
            context_input_limit=128_000,
            context_output_limit=64_000,
            cost_per_1m_input=Decimal("1.50"),
            cost_per_1m_output=Decimal("9.00"),
            cost_per_1m_cached=None,
            transport="websocket",
            verify_at_startup=False,
            enabled=True,
        ),
        "live-thinking": ModelSpec(
            alias="live-thinking",
            model_id="gemini-3.8-live-extended-thinking",
            fallback_alias="live",
            context_input_limit=128_000,
            context_output_limit=64_000,
            cost_per_1m_input=Decimal("1.50"),
            cost_per_1m_output=Decimal("9.00"),
            cost_per_1m_cached=None,
            transport="websocket",
            verify_at_startup=False,
            enabled=True,
        ),
        # Decision model (ADR-0029): advisory yes/no, pick-one and score judgements through its own
        # REST endpoint (app.decisions), not generateContent, so the generate gateway refuses it.
        "decision": ModelSpec(
            alias="decision",
            model_id="gemini-decision-1",
            fallback_alias=None,
            context_input_limit=262_144,
            context_output_limit=None,
            cost_per_1m_input=Decimal("0.10"),
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="decision",
            verify_at_startup=False,
            enabled=True,
            context_unverified=True,
        ),
        # Managed async agent (no automatic fallback; plan specifies 'with a custom plan')
        "research": ModelSpec(
            alias="research",
            model_id="deep-research-preview-04-2026",
            fallback_alias=None,
            context_input_limit=0,
            context_output_limit=None,
            cost_per_1m_input=None,
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="managed_agent",
            verify_at_startup=False,
            enabled=True,
            price_unknown=True,
            context_unverified=True,
        ),
        # Post-MVP audio model
        "tts": ModelSpec(
            alias="tts",
            model_id="gemini-3.8-flash-tts",
            fallback_alias=None,
            context_input_limit=32_000,
            context_output_limit=None,
            cost_per_1m_input=None,
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            transport="unsupported",
            verify_at_startup=False,
            is_post_mvp=True,
            enabled=False,
            price_unknown=True,
            context_unverified=True,
        ),
    }
    registry = ModelRegistry(specs)
    if overrides:
        registry = registry.with_overrides(overrides)
    registry.validate_configuration()
    return registry
