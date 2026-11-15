"""Search provider names, engine labels and the validation of ``MAYA_SEARCH_PROVIDERS`` (no heavy
imports).

Google Search grounding (``gemini``) is a provider like the others but it is OFF by default: the
default chain uses only free engines that need no key. The owner opts in by naming ``gemini`` in
``MAYA_SEARCH_PROVIDERS``.
"""

from __future__ import annotations

from typing import Final

PROVIDER_NAMES: Final = (
    "ddgs",
    "searxng",
    "brave",
    "tavily",
    "wikipedia",
    "hn",
    "arxiv",
    "stackexchange",
    "gemini",
)
# Intent providers (hn, arxiv, stackexchange) answer only a query with a clear signal for them and
# are tried first then; for every other query they are skipped. ``gemini`` is not in the default.
DEFAULT_PROVIDERS: Final = "hn,arxiv,stackexchange,ddgs,searxng,brave,tavily,wikipedia"
# Engines the ddgs library is asked for, in rotation order. Google is never in the rotation.
DDGS_ENGINES: Final = ("duckduckgo", "bing", "brave", "yahoo", "mojeek")
DDGS_ENGINE_CHOICES: Final = (*DDGS_ENGINES, "startpage", "grokipedia", "wikipedia")

# What the user sees for the engine that really answered. "Google Search" appears only when the
# opt-in Gemini grounding provider answered.
ENGINE_LABELS: Final = {
    "duckduckgo": "DuckDuckGo",
    "bing": "Bing",
    "brave": "Brave",
    "yahoo": "Yahoo",
    "mojeek": "Mojeek",
    "startpage": "Startpage",
    "grokipedia": "Grokipedia",
    "wikipedia": "Wikipedia",
    "searxng": "SearXNG",
    "tavily": "Tavily",
    "hn": "Hacker News",
    "arxiv": "arXiv",
    "stackexchange": "Stack Exchange",
    "gemini": "Google Search",
    "ddgs": "DuckDuckGo",
}


def engine_label(engine: str | None) -> str:
    """The display name of an engine key (``searxng:host`` is shown as SearXNG)."""
    if not engine:
        return "search"
    key = engine.split(":", 1)[0]
    return ENGINE_LABELS.get(key, engine)


def parse_provider_list(raw: str) -> list[str]:
    """The ordered provider names from a comma list; an unknown or repeated name is an error."""
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in PROVIDER_NAMES:
            raise ValueError(
                f"Unknown search provider '{name}' in MAYA_SEARCH_PROVIDERS. "
                f"Use only: {', '.join(PROVIDER_NAMES)}."
            )
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError(
            "MAYA_SEARCH_PROVIDERS is empty. Use a comma list of: "
            + ", ".join(PROVIDER_NAMES)
            + "."
        )
    return names


def parse_engine_list(raw: str) -> list[str]:
    """The ordered ddgs engines from a comma list (``MAYA_DDGS_ENGINES``); google is refused."""
    engines: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in DDGS_ENGINE_CHOICES:
            raise ValueError(
                f"Unknown search engine '{name}' in MAYA_DDGS_ENGINES. "
                f"Use only: {', '.join(DDGS_ENGINE_CHOICES)}."
            )
        if name not in engines:
            engines.append(name)
    if not engines:
        raise ValueError("MAYA_DDGS_ENGINES is empty. Use a comma list of engines.")
    return engines
