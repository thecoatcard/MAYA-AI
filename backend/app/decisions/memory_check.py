"""Memory save check with the decision model (ADR-0029).

Before a memory is saved: is it a secret or credential (refuse, in addition to the regex check),
and is it a durable preference or fact rather than a one-off (recorded as a hint only; the user
asked to save it, so it is still saved). No answer changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.decisions import runtime as decisions
from app.decisions.questions import is_no, noul, untrusted_block

SECRET_THRESHOLD = 0.85
ONE_OFF_THRESHOLD = 0.9


@dataclass(frozen=True)
class MemoryCheck:
    secret: bool = False
    one_off: bool = False


async def check_memory(text: str) -> MemoryCheck | None:
    if not decisions.available("memory"):
        return None
    answers = await decisions.decide(
        "memory",
        untrusted_block("memory text", text, limit=2000),
        [
            noul(
                "contains_secret",
                "Does the text contain a secret or credential (password, API key, token, "
                "private key, card number, one-time code)?",
            ),
            noul(
                "durable",
                "Is the text a durable preference or fact about the user that stays true "
                "over time, rather than a one-off task, a temporary state or a question?",
            ),
        ],
    )
    if answers is None:
        return None
    return MemoryCheck(
        secret=answers.yes("contains_secret", SECRET_THRESHOLD),
        one_off=is_no(answers.noul("durable"), ONE_OFF_THRESHOLD),
    )
