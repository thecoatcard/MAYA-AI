"""Deterministic PII and secret scrubber.

Neutralizes credit cards (Luhn validated), credentials, tokens, and SSNs
prior to long-term storage or embedding ingestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_REDACTED_CC: Final[str] = "[REDACTED_CREDIT_CARD]"
_REDACTED_SECRET: Final[str] = "[REDACTED_SECRET]"
_REDACTED_SSN: Final[str] = "[REDACTED_SSN]"

_PEM_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_JWT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bBearer\s+([A-Za-z0-9_\-\.~+/]+=*)")
_AWS_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b")
_GITHUB_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"
)
# Notion integration secrets: ``secret_<43 chars>`` (older) and ``ntn_<~44 chars>`` (current).
_NOTION_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:secret_[A-Za-z0-9]{43}|ntn_[A-Za-z0-9]{30,80})\b"
)
_SSN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_CANDIDATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


@dataclass(frozen=True)
class ScrubResult:
    clean_text: str
    redactions_count: int
    detected_types: list[str]


def verify_luhn(card_number: str) -> bool:
    """Validate card number using the standard Luhn checksum algorithm."""
    digits_only = re.sub(r"\D", "", card_number)
    if not (13 <= len(digits_only) <= 19) or not digits_only.isdigit():
        return False

    total = 0
    reversed_digits = digits_only[::-1]
    for i, char in enumerate(reversed_digits):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit

    return total % 10 == 0


class PiiScrubber:
    """Deterministic scrubber neutralizing sensitive PII and secrets."""

    def scrub(self, text: str) -> ScrubResult:
        """Scrub credit cards, secrets, tokens, and SSNs from text."""
        if not text:
            return ScrubResult(clean_text=text, redactions_count=0, detected_types=[])

        modified = text
        redactions = 0
        detected_types: set[str] = set()

        # 1. PEM Private Keys
        modified, count = _PEM_KEY_PATTERN.subn(_REDACTED_SECRET, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")

        # 2. JWTs
        modified, count = _JWT_PATTERN.subn(_REDACTED_SECRET, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")

        # 3. Bearer Tokens
        def _replace_bearer(match: re.Match[str]) -> str:
            token = match.group(1)
            if token == _REDACTED_SECRET:
                return match.group(0)
            return f"Bearer {_REDACTED_SECRET}"

        modified, count = _BEARER_PATTERN.subn(_replace_bearer, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")

        # 4. AWS Keys
        modified, count = _AWS_KEY_PATTERN.subn(_REDACTED_SECRET, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")

        # 5. GitHub Tokens
        modified, count = _GITHUB_TOKEN_PATTERN.subn(_REDACTED_SECRET, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")
        modified, count = _NOTION_TOKEN_PATTERN.subn(_REDACTED_SECRET, modified)
        if count > 0:
            redactions += count
            detected_types.add("secret")

        # 6. SSN
        modified, count = _SSN_PATTERN.subn(_REDACTED_SSN, modified)
        if count > 0:
            redactions += count
            detected_types.add("ssn")

        # 7. Credit Cards (Luhn validated)
        cc_redactions = 0

        def _replace_cc(match: re.Match[str]) -> str:
            nonlocal cc_redactions
            candidate = match.group(0)
            digits = re.sub(r"\D", "", candidate)
            if 13 <= len(digits) <= 19 and verify_luhn(digits):
                cc_redactions += 1
                detected_types.add("credit_card")
                return _REDACTED_CC
            return candidate

        modified = _CC_CANDIDATE_PATTERN.sub(_replace_cc, modified)
        redactions += cc_redactions

        return ScrubResult(
            clean_text=modified,
            redactions_count=redactions,
            detected_types=sorted(detected_types),
        )
