"""Resolve "send it to the same mail" from the conversation, and confirm it on a card.

An email recipient must be written in the user's own message (an address from a web page or a
tool result is never used silently). When the user refers to an earlier address ("same mail",
"usi ko", "us address par") the candidate comes from what was said earlier in THIS conversation
and is shown on a short ask card ("Send to <address>?") first. The user's answer then contains
the address, so the usual "written in the user's message" rule is satisfied by the user's own
reply, never by a guess.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Final

_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SAME: Final = re.compile(
    r"(?<!\w)(?:same|wahi|wohi|usi|isi|us|is|pehle\s+wale|upar\s+wale)\s+"
    r"(?:mail|email|e-mail|address|id|person|recipient|ko|par|pe|wale)(?!\w)"
    r"|(?<!\w)(?:usi|isi|wahi)\s+(?:ko|par|pe)(?!\w)|(?<!\w)same\s+one(?!\w)",
    re.IGNORECASE,
)
OTHER_LABEL: Final = "A different address"


def references_earlier_recipient(text: str) -> bool:
    """True when the message points at an address from earlier ("same mail", "usi ko")."""
    return _SAME.search(text or "") is not None and _EMAIL.search(text or "") is None


def candidates(history: Sequence[str], preferred: str = "") -> list[str]:
    """Addresses found in earlier messages of this conversation, newest first, ``preferred``
    (the model's own proposal) first when it really appears there. Never invented."""
    found: list[str] = []
    for text in reversed(list(history)):
        for match in reversed(_EMAIL.findall(text or "")):
            if match.lower() not in (a.lower() for a in found):
                found.append(match)
    wanted = preferred.strip().lower()
    found.sort(key=lambda address: 0 if address.lower() == wanted else 1)
    return found[:3]


def questions(addresses: Sequence[str]) -> list[dict[str, Any]]:
    """The ask_user question: confirm the resolved address or give another one."""
    first = addresses[0]
    return [
        {
            "question": f"Send to {first}?",
            "header": "Recipient",
            "options": [{"label": address} for address in addresses] + [{"label": OTHER_LABEL}],
            "allow_text": True,
        }
    ]


__all__ = ["OTHER_LABEL", "candidates", "questions", "references_earlier_recipient"]
