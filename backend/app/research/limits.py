"""The caps of one deep-research run, read from settings (``MAYA_DEEP_RESEARCH_*``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResearchLimits:
    """Per-run caps. A run never exceeds them, whatever the model or the user asks for."""

    min_domains: int = 20
    max_sources: int = 40
    hard_max_sources: int = 60
    time_cap_seconds: int = 600
    max_model_calls: int = 60
    max_rounds: int = 2
    fetch_concurrency: int = 6
    max_per_domain: int = 2
    candidate_target: int = 80
    max_queries: int = 30
    max_questions: int = 10

    @classmethod
    def from_settings(cls, settings: Any) -> ResearchLimits:
        return cls(
            min_domains=int(settings.deep_research_min_domains),
            max_sources=int(settings.deep_research_max_sources),
            hard_max_sources=max(
                int(settings.deep_research_hard_max_sources),
                int(settings.deep_research_max_sources),
            ),
            time_cap_seconds=int(settings.deep_research_time_cap_seconds),
            max_model_calls=int(settings.deep_research_max_model_calls),
            max_rounds=int(settings.deep_research_max_rounds),
            fetch_concurrency=int(settings.deep_research_fetch_concurrency),
        )

    def sources_for(self, requested: int | None) -> int:
        """Pages to fetch: the default, or what the user asked for up to the hard cap."""
        if requested is None or requested <= 0:
            return self.max_sources
        return min(requested, self.hard_max_sources)

    def as_status(self) -> dict[str, int]:
        return {
            "min_domains": self.min_domains,
            "max_sources": self.max_sources,
            "hard_max_sources": self.hard_max_sources,
            "time_cap_seconds": self.time_cap_seconds,
            "max_model_calls": self.max_model_calls,
            "max_rounds": self.max_rounds,
        }
