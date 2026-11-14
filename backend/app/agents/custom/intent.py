"""Deterministic recognition of "create an agent" requests (any common wording, English / Hinglish).

A message such as "create an agent that reviews my Python files" or "mere liye ek agent banao jo
meri Python files ka code review kare" asks for a reusable CUSTOM AGENT (a draft the user
approves), never for a background task. Chat uses this to keep such messages away from the task
classifier and from ``submit_task``.
"""

from __future__ import annotations

import re

_NOUN = r"(?:custom\s+)?(?:agents?|assistants?|bots?|एजेंट)"
_VERB_EN = r"(?:create|make|build|add|set\s*up|design|define|draft|write|generate|need|want)"
_VERB_HI = (
    r"(?:ban[ao]+|bana\s*(?:do|dena|dijiye|de)|banaiye|banana|bnao|bna\s*do|tayyar|"
    r"जोड़|बनाओ|बना\s*दो|बनाइए)"
)
# "create ... agent" within a few words, or "agent ... banao" (Hinglish verb after the noun).
_PATTERNS = (
    re.compile(rf"\b{_VERB_EN}\b(?:\W+\w+){{0,6}}?\W+{_NOUN}\b", re.IGNORECASE),
    re.compile(rf"\b{_NOUN}\b(?:\W+[\w-]+){{0,12}}?\W+{_VERB_HI}", re.IGNORECASE),
    re.compile(rf"{_NOUN}\W+(?:ban[ao]+|bana)\b", re.IGNORECASE),
    re.compile(rf"\bnew\s+{_NOUN}\b", re.IGNORECASE),
)
# "run the agent", "ask my reviewer agent ..." are not creation requests.
_NOT = re.compile(
    r"\b(?:run|use|call|ask|start|open|delete|remove|list|show)\b\s+(?:my|the)\b", re.I
)


def is_create_agent_request(text: str) -> bool:
    """True when ``text`` asks to create / make / build an agent (assistant, bot) for some job."""
    value = (text or "").strip()
    if not value or len(value) > 2_000:
        return False
    if _NOT.match(value) and not re.search(rf"\b{_VERB_EN}\b", value, re.IGNORECASE):
        return False
    return any(p.search(value) for p in _PATTERNS)
