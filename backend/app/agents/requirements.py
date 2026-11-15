"""The user's own requirements, carried verbatim through task decomposition.

The planner rewrites each subtask instruction, so the original wording (colours, theme, tone,
layout, audience, language, length, format) used to be lost and agents produced generic output.
``extract_requirements`` keeps the user's request verbatim (bounded) and adds a ``style`` object
found by deterministic rules (hex colours, colour words, theme words, language, tone, density,
layout, audience). ``attach_requirements`` stores it in every node's ``input_data`` (JSON, no
migration) and ``requirements_block`` renders the prompt block every agent prompt includes.

The user's own request is authoritative, not untrusted: only web text and tool output are.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

MAX_REQUEST_CHARS = 4_000
REQUIREMENTS_KEY = "requirements"

_HEX = re.compile(r"(?<![0-9A-Za-z])#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")
_COLOR_WORDS: dict[str, str] = {
    "blue": "#1D4ED8",
    "navy": "#12294D",
    "dark blue": "#12294D",
    "green": "#0F766E",
    "teal": "#0F766E",
    "red": "#B91C1C",
    "orange": "#C2410C",
    "purple": "#6D28D9",
    "violet": "#6D28D9",
    "pink": "#BE185D",
    "yellow": "#A16207",
    "gold": "#B8860B",
    "black": "#111827",
    "grey": "#4B5563",
    "gray": "#4B5563",
}
# Colour words become palette ROLES (background / text / heading / accent), not only an accent.
# ``_DARK_BG`` is a very dark shade of the colour for a dark page, ``_LIGHT_FG`` a light tint of it
# that has AA contrast on that dark page (headings and accents).
_DARK_BG: dict[str, str] = {
    "blue": "#0B1B3A",
    "navy": "#0A1633",
    "green": "#06241C",
    "teal": "#06262A",
    "red": "#2A0A0A",
    "orange": "#2B1405",
    "purple": "#1C0F3A",
    "violet": "#1C0F3A",
    "pink": "#2B0B1E",
    "yellow": "#2B2305",
    "gold": "#2B2305",
    "black": "#0A0A0A",
    "grey": "#1F2937",
    "gray": "#1F2937",
}
_LIGHT_FG: dict[str, str] = {
    "blue": "#93C5FD",
    "navy": "#93B4F0",
    "green": "#86EFAC",
    "teal": "#5EEAD4",
    "red": "#FCA5A5",
    "orange": "#FDBA74",
    "purple": "#C4B5FD",
    "violet": "#C4B5FD",
    "pink": "#F9A8D4",
    "yellow": "#FDE047",
    "gold": "#FCD34D",
    "black": "#E5E7EB",
    "grey": "#D1D5DB",
    "gray": "#D1D5DB",
}
_COLOR_NAMES = "navy|blue|green|teal|red|orange|purple|violet|pink|yellow|gold|black|grey|gray"
_COLOR_PHRASE = re.compile(rf"\b(?:(dark|deep)\s+)?({_COLOR_NAMES})\b", re.IGNORECASE)
_DARK_PAGE = re.compile(
    r"\bdark\s+(?:theme|mode|background)\b|\bnight\s+(?:theme|mode)\b|"
    r"\b(?:theme|mode|background)\s+(?:is\s+)?dark\b|\bblack\s+background\b",
    re.IGNORECASE,
)
_THEME_TAIL = re.compile(r"^(?:\W+\w+){0,3}?\W+(?:theme|mode|background)\b", re.IGNORECASE)
_THEME_WORDS: tuple[tuple[str, str], ...] = (
    (
        r"\bdark\b(?!\s+(?:blue|green|red|orange|purple|grey|gray|teal|pink))|\bnight\b|"
        r"\bmidnight\b",
        "midnight",
    ),
    (r"\bminimal(?:ist|istic)?\b|\bclean and simple\b|\bairy\b", "minimal"),
    (r"\bexecutive\b|\bformal\b|\bcorporate\b", "executive"),
    (r"\bnotes\b|\bcheat ?sheet\b|\bstudy\b", "notes"),
)
_LANGUAGE: tuple[tuple[str, str], ...] = (
    (r"\bhinglish\b|\bhindi\s*\+\s*english\b", "Hinglish"),
    (r"\bin hindi\b|\bhindi mein\b|\bहिंदी\b", "Hindi"),
    (r"\bin english\b", "English"),
)
_TONE: tuple[tuple[str, str], ...] = (
    (r"\bsimple words?\b|\bsimple language\b|\beasy language\b|\bseedha\b", "simple words"),
    (r"\bformal\b|\bprofessional\b", "formal"),
    (r"\bcasual\b|\bfriendly\b|\bconversational\b", "casual"),
    (r"\bfunny\b|\bhumou?r\b", "light humour"),
)
_LAYOUT: tuple[tuple[str, str], ...] = (
    (r"\btwo[- ]column\b|\b2[- ]column\b", "two columns"),
    (r"\bone[- ]pager\b|\bone page\b", "one page"),
    (r"\bbullet(?:s| points?)\b", "bullet points"),
    (r"\btables?\b", "tables"),
    (r"\bno images?\b|\bwithout images?\b", "no images"),
)
_AUDIENCE = re.compile(
    r"\b(?:for|audience[: ]+)\s+((?:absolute |complete )?(?:beginners?|students?|kids|children|"
    r"managers?|executives?|developers?|engineers?|investors?|customers?|clients?|teachers?|"
    r"experts?))\b",
    re.IGNORECASE,
)


def _first(rules: Iterable[tuple[str, str]], text: str) -> str | None:
    for pattern, value in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return value
    return None


def _normalise_hex(raw: str) -> str:
    digits = raw.upper()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits}"


_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH_MARKERS = re.compile(
    r"\b(?:hai|hain|kya|kaise|karo|kare|kijiye|banao|bana do|mujhe|mere|aur|ke liye|ka|ki|ko|se|"
    r"mein|me|nahi|samjhao|batao|likho|chahiye|wala|wali)\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """The language of the user's request: ``Hindi``, ``Hinglish`` or ``English`` (the default).

    An explicit mention ("in Hinglish", "in Hindi") wins; otherwise Devanagari means Hindi and
    two or more Roman-script Hindi marker words mean Hinglish. Used to pin the language of every
    generated chapter to the language of the request, so an English request never drifts.
    """
    explicit = _first(_LANGUAGE, text or "")
    if explicit:
        return explicit
    if _DEVANAGARI.search(text or ""):
        return "Hindi"
    if len(_HINGLISH_MARKERS.findall(text or "")) >= 2:
        return "Hinglish"
    return "English"


def language_pin(text: str) -> str:
    """One prompt line that fixes the output language and forbids mixing."""
    language = detect_language(text)
    extra = (
        " Keep technical terms and code in English."
        if language in ("Hindi", "Hinglish")
        else " Do not use Hindi or Hinglish words anywhere."
    )
    return (
        f"LANGUAGE (fixed): write every sentence of the output in {language} only, from the first "
        f"word to the last. Never mix in another language or switch half-way.{extra}"
    )


def extract_palette_roles(text: str) -> dict[str, str] | None:
    """Colour words in ``text`` as palette roles, or ``None`` when no colour word is there.

    * "dark blue theme", "navy background", "dark mode, blue": a DARK page: dark background of the
      colour, light text, the colour's light tint for headings and accents.
    * "blue", "green report", "dark blue headings": a LIGHT page whose headings and accents use the
      colour (never just a thin accent line under black headings).
    A stated hex colour is left to ``accent_color``. Contrast is repaired again at render time.
    """
    match = _COLOR_PHRASE.search(text)
    if match is None or _HEX.search(text):
        return None
    name = match.group(2).lower()
    modifier = (match.group(1) or "").lower()
    tail = text[match.end() :]
    dark_page = bool(_DARK_PAGE.search(text)) or (
        (modifier == "dark" or name == "navy") and bool(_THEME_TAIL.match(tail))
    )
    if dark_page:
        light = _LIGHT_FG[name]
        return {
            "background": _DARK_BG[name],
            "text": "#E8EEF7",
            "muted": "#AEB9CC",
            "heading": light,
            "accent": light,
            "primary": light,
        }
    ink = _COLOR_WORDS["dark blue" if (modifier == "dark" and name == "blue") else name]
    return {
        "background": "#FFFFFF",
        "text": "#1F2937",
        "heading": ink,
        "accent": ink,
        "primary": ink,
    }


def extract_style(text: str) -> dict[str, Any]:
    """The renderable and tonal style the user stated, from deterministic rules."""
    style: dict[str, Any] = {}
    lowered = text.lower()
    hexes = [_normalise_hex(m.group(1)) for m in _HEX.finditer(text)]
    if hexes:
        style["accent_color"] = hexes[0]
        if len(hexes) > 1:
            style["palette"] = list(dict.fromkeys(hexes))[:6]
    else:
        for word in sorted(_COLOR_WORDS, key=len, reverse=True):
            if re.search(rf"\b{word}\b", lowered):
                style["accent_color"] = _COLOR_WORDS[word]
                break
    roles = None if hexes else extract_palette_roles(text)
    if roles:
        style["palette_roles"] = roles
        style["accent_color"] = roles["accent"].upper()
    if (theme := _first(_THEME_WORDS, text)) is not None:
        style["theme"] = theme
    if (language := _first(_LANGUAGE, text)) is not None:
        style["language"] = language
    if (tone := _first(_TONE, text)) is not None:
        style["tone"] = tone
    if (layout := _first(_LAYOUT, text)) is not None:
        style["layout"] = layout
    if re.search(r"\bcompact\b|\bdense\b|\btight\b", lowered):
        style["density"] = "compact"
    audience = _AUDIENCE.search(text)
    if audience:
        style["audience"] = audience.group(1).lower()
    return style


def extract_requirements(
    user_request: str, style_hint: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``{"user_request": <verbatim, bounded>, "style": {...}}`` for a task."""
    text = (user_request or "").strip()[:MAX_REQUEST_CHARS]
    style = extract_style(text)
    for key, value in (style_hint or {}).items():
        if value not in (None, "", [], {}):
            style[key] = value
    style.setdefault("other", [])
    return {"user_request": text, "style": style}


def attach_requirements(nodes: Iterable[Any], requirements: dict[str, Any]) -> None:
    """Put ``requirements`` on every node's ``input_data`` (re-attach when a repair dropped it)."""
    for node in nodes:
        data = node.input_data if node.input_data is not None else {}
        data[REQUIREMENTS_KEY] = requirements
        node.input_data = data


PREFERENCES_KEY = "user_preferences"
"""``input_data`` key holding the user's standing-preferences block (a ready, escaped string)."""


def attach_preferences(nodes: Iterable[Any], block: str) -> None:
    """Put the standing-preferences ``block`` on every node (nothing when it is empty)."""
    if not block:
        return
    for node in nodes:
        data = node.input_data if node.input_data is not None else {}
        data[PREFERENCES_KEY] = block
        node.input_data = data


def requirements_block(input_data: dict[str, Any] | None) -> str:
    """The prompt block with the user's requirements and saved preferences (``""`` when none)."""
    prefs = (input_data or {}).get(PREFERENCES_KEY)
    prefs_text = f"\n{prefs}\n" if isinstance(prefs, str) and prefs.strip() else ""
    return _requirements_text(input_data) + prefs_text


def _requirements_text(input_data: dict[str, Any] | None) -> str:
    req = (input_data or {}).get(REQUIREMENTS_KEY)
    if not isinstance(req, dict) or not str(req.get("user_request") or "").strip():
        return ""
    lines = [
        "USER REQUIREMENTS (verbatim, authoritative; follow them over defaults. This is the "
        "user's own request, not untrusted data):",
        str(req["user_request"]).strip()[:MAX_REQUEST_CHARS],
    ]
    raw_style = req.get("style")
    style: dict[str, Any] = raw_style if isinstance(raw_style, dict) else {}

    def shown(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(map(str, value))
        if isinstance(value, dict):
            return ", ".join(f"{k} {v}" for k, v in value.items())
        return str(value)

    parts = [
        f"{key}: {shown(value)}" for key, value in style.items() if value not in (None, "", [], {})
    ]
    if parts:
        lines.append("Style the user asked for: " + "; ".join(parts))
    return "\n" + "\n".join(lines) + "\n"
