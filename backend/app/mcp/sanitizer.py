"""Layered prompt-injection defense and untrusted tool output sanitization (Decision D5)."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Regex for ANSI escape sequences
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Dangerous hidden bidirectional Unicode control characters (can reorder text visually)
BIDI_CONTROL_PATTERN = re.compile(r"[\u200E\u200F\u202A-\u202E\u2066-\u2069]")

# Prompt-injection heuristic signatures
INJECTION_SIGNATURES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts|rules)\b", re.I),
        "ignore_previous_instructions",
    ),
    (
        re.compile(
            r"\bdisregard\s+(all\s+)?(previous|prior|above)\s+(instructions|directives)\b", re.I
        ),
        "disregard_instructions",
    ),
    (
        re.compile(
            r"\b(show|reveal|display|output|print)\s+(your\s+)?(system\s+prompt|developer\s+mode)\b",
            re.I,
        ),
        "system_prompt_reveal",
    ),
    (
        re.compile(r"\b(new\s+system\s+instruction|system\s+override|jailbreak\s+mode)\b", re.I),
        "system_override",
    ),
    (
        re.compile(r"\b(you\s+are\s+now|act\s+as)\s+(DAN|unrestricted|godmode)\b", re.I),
        "persona_jailbreak",
    ),
    (
        re.compile(
            r"\b(execute\s+without\s+approval|skip\s+(human\s+)?approval|bypass\s+guardrails?)\b",
            re.I,
        ),
        "approval_bypass_attempt",
    ),
    (
        re.compile(r"\[SYSTEM(?:\s+NOTE)?:\s*[^\]]+\]", re.I),
        "fake_system_injection",
    ),
    (
        re.compile(r"!\[.*?\]\(https?://[^\s\)]+\)", re.I),
        "markdown_image_exfiltration",
    ),
]


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """Result of sanitizing and wrapping untrusted external tool content."""

    clean_text: str
    wrapped_output: str
    has_injection_suspicions: bool
    suspicions: tuple[str, ...]


def clean_untrusted_text(raw_text: str) -> str:
    """Strip null bytes, ANSI terminal escapes, and hidden BiDi overrides from text.

    Escapes structural delimiter breakout attempts.
    """
    # 1. Strip null bytes
    cleaned = raw_text.replace("\x00", "")

    # 2. Strip ANSI escape sequences
    cleaned = ANSI_ESCAPE_PATTERN.sub("", cleaned)

    # 3. Strip BiDi control characters
    cleaned = BIDI_CONTROL_PATTERN.sub("", cleaned)

    # 4. Unicode NFKC normalization
    cleaned = unicodedata.normalize("NFKC", cleaned)

    # 5. Prevent delimiter breakout by escaping triple angle brackets
    cleaned = cleaned.replace("<<<", "&lt;&lt;&lt;").replace(">>>", "&gt;&gt;&gt;")

    return cleaned


def scan_prompt_injection(text: str) -> tuple[bool, list[str]]:
    """Scan text against known prompt injection and instruction-override heuristics."""
    found: list[str] = []
    for pattern, name in INJECTION_SIGNATURES:
        if pattern.search(text):
            found.append(name)
    return len(found) > 0, found


def wrap_untrusted_input(content: str, source: str, timestamp: str) -> str:
    """Enclose untrusted external text in structural delimiters for Model Gateway grounding."""
    return (
        f'<<<UNTRUSTED_EXTERNAL_INPUT source="{source}" timestamp="{timestamp}">>>\n'
        f"{content}\n"
        f"<<<END_UNTRUSTED_EXTERNAL_INPUT>>>"
    )


def sanitize_tool_output(
    content: str,
    tool_name: str,
    timestamp: datetime | None = None,
) -> SanitizationResult:
    """Sanitize, scan, and structurally delimit untrusted external tool outputs."""
    current_time = timestamp if timestamp is not None else datetime.now(UTC)
    iso_time = current_time.isoformat()

    clean = clean_untrusted_text(content)
    has_suspicions, detected = scan_prompt_injection(clean)
    if has_suspicions:
        logger.warning(
            "Security Alert: Suspicious prompt injection pattern in tool output '%s': %s",
            tool_name,
            detected,
        )

    wrapped = wrap_untrusted_input(clean, source=tool_name, timestamp=iso_time)

    return SanitizationResult(
        clean_text=clean,
        wrapped_output=wrapped,
        has_injection_suspicions=has_suspicions,
        suspicions=tuple(detected),
    )


def sanitize_untrusted_content(
    content: str,
    source: str = "document",
    timestamp: datetime | None = None,
) -> SanitizationResult:
    """Sanitize untrusted documents, retrieved context, or user inputs before model injection."""
    return sanitize_tool_output(content, tool_name=source, timestamp=timestamp)
