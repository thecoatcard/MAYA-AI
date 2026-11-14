"""What a deep-research message asks for, decided by code (no model): the topic, how many sites,
whether a topic is missing, and the system-written start message (never model text)."""

from __future__ import annotations

import re
from typing import Final

from app.research.limits import ResearchLimits

_SITES = re.compile(
    r"\b(\d{1,3})\s*\+?\s*(?:different\s+|distinct\s+|unique\s+)?"
    r"(?:sites|websites|web\s*sites|sources|pages|links|urls|webpages)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Words that carry no topic by themselves ("deep research karo", "please research this").
_FILLER: Final = frozenset(
    "a an the of on about for to in me my please pls can you could would will do does did is are "
    "research researching deep dive investigate investigation study find out look into learn "
    "tell give write make create prepare report this that it these those some any thing things "
    "topic subject and or with karo kar karna do dena de dijiye kijiye mujhe mere humein hamein "
    "ke ki ka ko par pe per mein me baare baarein bare vishay isko iska usko uska ye yeh woh wo "
    "kuch sab aur se hai hain tha thi".split()
)
TOPIC_CHARS: Final = 200


def requested_sources(text: str) -> int | None:
    """ "read 50 sites" -> 50. ``None`` when the user named no number."""
    best: int | None = None
    for match in _SITES.finditer(text):
        value = int(match.group(1))
        if value >= 5:  # "2 sources" is a quote, not a research size
            best = max(best or 0, value)
    return best


def needs_topic(text: str) -> bool:
    """True when the message has no subject to research (``deep research``, ``research karo``)."""
    words = [w.lower() for w in _WORD.findall(text)]
    return not [w for w in words if w not in _FILLER and not w.isdigit()]


def topic_of(text: str) -> str:
    return " ".join(text.split())[:TOPIC_CHARS]


def missing_topic_question() -> str:
    return "What should I research? Tell me the topic (and the format you want, if any)."


def start_text(topic: str, limits: ResearchLimits, requested: int | None = None) -> str:
    """The honest message written by code when the run starts. It promises effort, never results."""
    cap = limits.sources_for(requested)
    minimum = min(limits.min_domains, cap)
    return (
        f"Started: deep research on {topic}. I will read at least {minimum} different sites "
        f"(up to {cap}), analyse them and write the result with numbered sources. This can take "
        f"up to {max(1, limits.time_cap_seconds // 60)} minutes; I update you here as I go and "
        "will not share any findings until it finishes. Pages behind a paywall or login are "
        "skipped and reported."
    )


def refusal_text(reason: str) -> str:
    return f"Deep research did not start: {reason}. Nothing was searched or read."
