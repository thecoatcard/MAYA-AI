"""Dependency-free HTML layout for the system e-mails MAYA itself sends.

One shared, table-based layout (works in Outlook, Gmail and Apple Mail), inline styles first with
a small ``<style>`` block only for dark mode and phones. No remote images, fonts, links or tracking:
the brand mark is plain coloured table cells. Every interpolated value goes through
``html.escape``; callers pass plain text, never markup.

Palette mapping (e-mail cannot use CSS variables, so the values are copied from
``apps/web/src/app/globals.css`` and must be updated together with it):

    page_bg      --maya-bg            (light #f1f2f5, dark #0b0d11)
    card_bg      --maya-pane          (light #ffffff, dark #12151b)
    text         --maya-text          (light #101216, dark #e8eaee)
    secondary    --maya-text-secondary(light #343944, dark #c2c7d1)
    muted        --maya-text-muted    (light #525967, dark #98a0ae)
    border       --maya-border        (light #d3d6dd, dark #272c36)
    accent       --maya-accent        (light #3046d4, dark #8da0ff)   ultramarine family
    accent_soft  --maya-accent-soft   (light #e2e6fb, dark #1b2250)
    accent_text  --maya-accent-text   (light #ffffff, dark #0a1030)
    radii        --maya-radius-md 12px (code box), --maya-radius-lg 18px (card)
    fonts        --maya-font-sans / --maya-font-mono with Segoe UI / Arial / Consolas fallbacks

Clients that ignore both ``color-scheme`` and ``prefers-color-scheme`` (Outlook desktop, some Gmail
views) get the light palette, which is what is written inline.
"""

import re
from dataclasses import dataclass, field
from html import escape
from typing import Final, Literal

MAX_WIDTH_PX: Final = 560


@dataclass(frozen=True)
class Palette:
    page_bg: str
    card_bg: str
    text: str
    secondary: str
    muted: str
    border: str
    accent: str
    accent_soft: str
    accent_text: str

    def contrast_pairs(self) -> tuple[tuple[str, str, str], ...]:
        """(label, foreground, background) pairs that must reach 4.5:1."""
        return (
            ("heading on card", self.text, self.card_bg),
            ("body on card", self.secondary, self.card_bg),
            ("muted on card", self.muted, self.card_bg),
            ("code on code box", self.text, self.accent_soft),
            ("mark letter on accent", self.accent_text, self.accent),
            ("wordmark on card", self.text, self.card_bg),
            ("footer on page", self.muted, self.page_bg),
        )


LIGHT: Final = Palette(
    page_bg="#f1f2f5",
    card_bg="#ffffff",
    text="#101216",
    secondary="#343944",
    muted="#525967",
    border="#d3d6dd",
    accent="#3046d4",
    accent_soft="#e2e6fb",
    accent_text="#ffffff",
)
DARK: Final = Palette(
    page_bg="#0b0d11",
    card_bg="#12151b",
    text="#e8eaee",
    secondary="#c2c7d1",
    muted="#98a0ae",
    border="#272c36",
    accent="#8da0ff",
    accent_soft="#1b2250",
    accent_text="#0a1030",
)

_SANS: Final = "IBM Plex Sans,Segoe UI,Arial,sans-serif"  # unquoted: lives in style='...'
_MONO: Final = "IBM Plex Mono,Consolas,Courier New,monospace"
_FILLER: Final = "&#8204;&nbsp;" * 40  # keeps body text out of the inbox preview line


@dataclass(frozen=True)
class EmailContent:
    """Plain-text description of one system mail; the layout does all escaping."""

    title: str  # document title and heading
    preheader: str
    paragraphs: tuple[str, ...] = ()
    code: str | None = None
    code_label: str = "Your one-time code"
    notes: tuple[str, ...] = ()  # muted security guidance under the code
    username: str | None = None
    footer_reason: str = "You received this e-mail because of an action on your MAYA AI account."
    extra: tuple[str, ...] = field(default=())  # paragraphs shown after the code box


def _e(value: str) -> str:
    return escape(value, quote=True)


def _dark_rules(scope: str) -> str:
    d = DARK
    rules = (
        f".bg{{background-color:{d.page_bg} !important}}",
        f".card{{background-color:{d.card_bg} !important;border-color:{d.border} !important}}",
        f".tx{{color:{d.text} !important}}",
        f".sc{{color:{d.secondary} !important}}",
        f".mu{{color:{d.muted} !important}}",
        f".rule{{border-color:{d.border} !important}}",
        f".mark{{background-color:{d.accent} !important;color:{d.accent_text} !important}}",
        f".code{{background-color:{d.accent_soft} !important;color:{d.text} !important}}",
    )
    return scope.replace("RULES", "".join(rules))


def render_html(
    content: EmailContent, *, force_scheme: Literal["light", "dark"] | None = None
) -> str:
    """Render ``content`` to one HTML document. ``force_scheme`` is for the preview tool only."""
    lt = LIGHT
    if force_scheme == "dark":
        dark_css = _dark_rules("RULES")
    elif force_scheme == "light":
        dark_css = ""
    else:
        dark_css = _dark_rules("@media (prefers-color-scheme:dark){RULES}")
    mobile_css = "@media only screen and (max-width:600px){.pad{padding:24px 20px !important}}"

    paragraphs = "".join(
        f"<p class='sc' style='margin:0 0 16px;font:400 16px/24px {_SANS};color:{lt.secondary}'>"
        f"{_e(text)}</p>"
        for text in content.paragraphs
    )
    code_block = ""
    if content.code is not None:
        code_block = (
            f"<p class='mu' style='margin:8px 0 8px;font:500 12px/16px {_SANS};"
            f"letter-spacing:0.08em;text-transform:uppercase;color:{lt.muted}'>"
            f"{_e(content.code_label)}</p>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0'>"
            f"<tr><td class='code' align='center' bgcolor='{lt.accent_soft}' "
            f"style='background-color:{lt.accent_soft};border-radius:12px;padding:20px 12px;"
            f"font:600 32px/40px {_MONO};letter-spacing:0.3em;color:{lt.text};"
            f"user-select:all;-webkit-user-select:all'>{_e(content.code)}</td></tr></table>"
            "<div style='height:20px;line-height:20px;font-size:0'>&nbsp;</div>"
        )
    extra = "".join(
        f"<p class='sc' style='margin:0 0 16px;font:400 16px/24px {_SANS};color:{lt.secondary}'>"
        f"{_e(text)}</p>"
        for text in content.extra
    )
    notes = "".join(
        f"<p class='mu' style='margin:0 0 8px;font:400 14px/20px {_SANS};color:{lt.muted}'>"
        f"{_e(text)}</p>"
        for text in content.notes
    )
    if content.username is not None:
        notes += (
            f"<p class='mu' style='margin:0;font:400 14px/20px {_SANS};color:{lt.muted}'>"
            f"Account username: <strong class='tx' style='font-weight:600;color:{lt.text}'>"
            f"{_e(content.username)}</strong></p>"
        )
    note_block = ""
    if notes:
        note_block = (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0'>"
            f"<tr><td class='rule' style='border-top:1px solid {lt.border};padding-top:16px'>"
            f"{notes}</td></tr></table>"
        )

    brand = (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0'><tr>"
        f"<td class='mark' width='32' height='32' align='center' valign='middle' "
        f"bgcolor='{lt.accent}' style='width:32px;height:32px;background-color:{lt.accent};"
        f"border-radius:10px;font:700 18px/32px {_SANS};color:{lt.accent_text}'>M</td>"
        f"<td class='tx' style='padding-left:12px;font:600 16px/32px {_SANS};"
        f"letter-spacing:0.12em;color:{lt.text}'>MAYA AI</td></tr></table>"
    )
    inner = (
        f"<tr><td class='pad' style='padding:32px 32px 8px'>{brand}</td></tr>"
        "<tr><td class='pad' style='padding:16px 32px 32px'>"
        f"<h1 class='tx' style='margin:0 0 16px;font:600 24px/32px {_SANS};"
        f"letter-spacing:-0.015em;color:{lt.text}'>{_e(content.title)}</h1>"
        f"{paragraphs}{code_block}{extra}{note_block}</td></tr>"
    )
    card = (
        f"<table role='presentation' class='card' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' bgcolor='{lt.card_bg}' style='max-width:{MAX_WIDTH_PX}px;"
        f"background-color:{lt.card_bg};border:1px solid {lt.border};border-radius:18px'>"
        f"{inner}</table>"
    )
    footer = (
        f"<p class='mu' style='margin:24px 0 4px;font:600 13px/18px {_SANS};color:{lt.muted}'>"
        "Sent by MAYA AI</p>"
        f"<p class='mu' style='margin:0;font:400 13px/18px {_SANS};color:{lt.muted}'>"
        f"{_e(content.footer_reason)} This is an automated message.</p>"
    )
    return (
        "<!DOCTYPE html>"
        "<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        "<meta name='supported-color-schemes' content='light dark'>"
        "<meta name='x-apple-disable-message-reformatting'>"
        f"<title>{_e(content.title)}</title>"
        f"<style>:root{{color-scheme:light dark;supported-color-schemes:light dark}}"
        f"{mobile_css}{dark_css}</style></head>"
        f"<body class='bg' style='margin:0;padding:0;background-color:{lt.page_bg}'>"
        f"<div style='display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;"
        f"line-height:1px;color:{lt.page_bg}'>{_e(content.preheader)}{_FILLER}</div>"
        f"<table role='presentation' class='bg' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' bgcolor='{lt.page_bg}' style='background-color:{lt.page_bg}'>"
        "<tr><td align='center' style='padding:32px 16px'>"
        f"<!--[if mso]><table role='presentation' width='{MAX_WIDTH_PX}' cellpadding='0' "
        "cellspacing='0' border='0'><tr><td><![endif]-->"
        f"{card}"
        "<!--[if mso]></td></tr></table><![endif]-->"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='max-width:{MAX_WIDTH_PX}px'><tr><td class='pad' style='padding:0 32px' "
        f"align='left'>{footer}</td></tr></table>"
        "</td></tr></table></body></html>"
    )


# --- scheduled_report (ADR-0033) -------------------------------------------------------------
# The same brand bar, palette, card and dark-mode rules as the one-time-code mails, with the
# report body rendered from Markdown by a small, escaping-first converter. Source links are plain
# https anchors the user can read; there are no images, remote CSS, fonts, scripts or trackers.

REPORT_MAX_CHARS: Final = 40_000  # plain-text limit of the report body
REPORT_MAX_HTML_BYTES: Final = 70_000  # body HTML stops here: inboxes clip near 100 KB
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d{1,2}[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


@dataclass(frozen=True)
class ReportMail:
    """Plain-text description of one scheduled report mail; the layout does all escaping."""

    title: str  # for example "Tech news - Friday, 14 November 2026"
    date_label: str
    preheader: str
    markdown: str
    account_email: str
    artifact_note: str | None = None  # shown when a file was made ("full report is in Artifacts")
    notes: tuple[str, ...] = ()  # extra honest lines, for example "Only 3 sources could be read"


def _inline(text: str) -> str:
    """Escape first, then turn the few supported inline marks into tags."""
    out = _e(text)
    out = _LINK.sub(
        lambda m: (
            f"<a href='{m.group(2)}' class='lk' rel='noopener noreferrer' "
            f"style='color:{LIGHT.accent};text-decoration:underline'>{m.group(1)}</a>"
        ),
        out,
    )
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


def _report_blocks(markdown: str, title: str) -> str:
    lt = LIGHT
    html: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    ordered = False
    skipped_title = False

    def flush_paragraph() -> None:
        if paragraph:
            html.append(
                f"<p class='sc' style='margin:0 0 14px;font:400 15px/23px {_SANS};"
                f"color:{lt.secondary}'>{_inline(' '.join(paragraph))}</p>"
            )
            paragraph.clear()

    def flush_list() -> None:
        if items:
            tag = "ol" if ordered else "ul"
            body = "".join(
                f"<li class='sc' style='margin:0 0 8px;font:400 15px/23px {_SANS};"
                f"color:{lt.secondary}'>{_inline(item)}</li>"
                for item in items
            )
            html.append(f"<{tag} style='margin:0 0 14px;padding-left:22px'>{body}</{tag}>")
            items.clear()

    cut = False
    for raw in markdown[:REPORT_MAX_CHARS].splitlines():
        pending = sum(len(i) + 240 for i in items) + sum(len(p) + 40 for p in paragraph)
        if sum(len(part) for part in html) + pending > REPORT_MAX_HTML_BYTES:
            cut = True
            break
        line = raw.rstrip()
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            level, text = len(heading.group(1)), heading.group(2).strip()
            if level == 1 and not skipped_title:
                skipped_title = True
                if text.lower() == title.lower() or title.lower() in text.lower():
                    continue
            if level <= 2:
                html.append(
                    f"<h2 class='tx rule' style='margin:24px 0 10px;padding-top:14px;"
                    f"border-top:1px solid {lt.border};font:600 18px/26px {_SANS};"
                    f"color:{lt.text}'>{_e(text)}</h2>"
                )
            else:
                html.append(
                    f"<h3 class='tx' style='margin:16px 0 6px;font:600 15px/22px {_SANS};"
                    f"color:{lt.text}'>{_e(text)}</h3>"
                )
        elif bullet or numbered:
            flush_paragraph()
            is_ordered = numbered is not None and bullet is None
            if items and is_ordered != ordered:
                flush_list()
            ordered = is_ordered
            match = bullet or numbered
            assert match is not None
            items.append(match.group(1).strip())
        elif not line.strip():
            flush_paragraph()
            flush_list()
        else:
            flush_list()
            paragraph.append(line.strip())
    flush_paragraph()
    flush_list()
    if cut or len(markdown) > REPORT_MAX_CHARS:
        html.append(
            f"<p class='mu' style='margin:0 0 14px;font:400 14px/20px {_SANS};"
            f"color:{LIGHT.muted}'>The rest of this report is in MAYA (chat or Artifacts).</p>"
        )
    return "".join(html)


def render_report_html(mail: ReportMail) -> str:
    """One HTML document for a scheduled report (light inline palette, dark mode by media query)."""
    lt = LIGHT
    brand = (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0'><tr>"
        f"<td class='mark' width='32' height='32' align='center' valign='middle' "
        f"bgcolor='{lt.accent}' style='width:32px;height:32px;background-color:{lt.accent};"
        f"border-radius:10px;font:700 18px/32px {_SANS};color:{lt.accent_text}'>M</td>"
        f"<td class='tx' style='padding-left:12px;font:600 16px/32px {_SANS};"
        f"letter-spacing:0.12em;color:{lt.text}'>MAYA AI</td>"
        f"<td class='mu' align='right' style='padding-left:16px;font:400 13px/32px {_SANS};"
        f"color:{lt.muted}'>{_e(mail.date_label)}</td></tr></table>"
    )
    extra_notes = "".join(
        f"<p class='mu' style='margin:0 0 8px;font:400 14px/20px {_SANS};color:{lt.muted}'>"
        f"{_e(note)}</p>"
        for note in mail.notes
    )
    artifact = ""
    if mail.artifact_note:
        artifact = (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' "
            "style='margin:8px 0 8px'>"
            f"<tr><td class='code' bgcolor='{lt.accent_soft}' style='background-color:"
            f"{lt.accent_soft};border-radius:12px;padding:14px 16px;font:500 14px/20px {_SANS};"
            f"color:{lt.text}'>{_e(mail.artifact_note)}</td></tr></table>"
        )
    body = _report_blocks(mail.markdown, mail.title)
    tail = ""
    if extra_notes:
        tail = (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0'>"
            f"<tr><td class='rule' style='border-top:1px solid {lt.border};padding-top:14px'>"
            f"{extra_notes}</td></tr></table>"
        )
    inner = (
        f"<tr><td class='pad' style='padding:28px 32px 4px'>{brand}</td></tr>"
        "<tr><td class='pad' style='padding:14px 32px 28px'>"
        f"<h1 class='tx' style='margin:0 0 14px;font:600 24px/32px {_SANS};"
        f"letter-spacing:-0.015em;color:{lt.text}'>{_e(mail.title)}</h1>"
        f"{artifact}{body}{tail}</td></tr>"
    )
    card = (
        f"<table role='presentation' class='card' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' bgcolor='{lt.card_bg}' style='max-width:{MAX_WIDTH_PX + 40}px;"
        f"background-color:{lt.card_bg};border:1px solid {lt.border};border-radius:18px'>"
        f"{inner}</table>"
    )
    footer = (
        f"<p class='mu' style='margin:24px 0 4px;font:600 13px/18px {_SANS};color:{lt.muted}'>"
        f"Sent by MAYA AI for {_e(mail.account_email)} &middot; a schedule you set up.</p>"
        f"<p class='mu' style='margin:0;font:400 13px/18px {_SANS};color:{lt.muted}'>"
        "Pause or delete it in Settings &gt; Schedules. This is an automated message.</p>"
    )
    dark = _dark_rules("@media (prefers-color-scheme:dark){RULES}").replace(
        "}}", "}.lk{color:" + DARK.accent + " !important}}", 1
    )
    mobile_css = "@media only screen and (max-width:600px){.pad{padding:22px 18px !important}}"
    return (
        "<!DOCTYPE html>"
        "<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        "<meta name='supported-color-schemes' content='light dark'>"
        "<meta name='x-apple-disable-message-reformatting'>"
        f"<title>{_e(mail.title)}</title>"
        f"<style>:root{{color-scheme:light dark;supported-color-schemes:light dark}}"
        f"{mobile_css}{dark}</style></head>"
        f"<body class='bg' style='margin:0;padding:0;background-color:{lt.page_bg}'>"
        f"<div style='display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;"
        f"line-height:1px;color:{lt.page_bg}'>{_e(mail.preheader)}{_FILLER}</div>"
        f"<table role='presentation' class='bg' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' bgcolor='{lt.page_bg}' style='background-color:{lt.page_bg}'>"
        "<tr><td align='center' style='padding:28px 12px'>"
        f"{card}"
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='max-width:{MAX_WIDTH_PX + 40}px'><tr><td class='pad' style='padding:0 32px' "
        f"align='left'>{footer}</td></tr></table>"
        "</td></tr></table></body></html>"
    )


def render_report_text(mail: ReportMail) -> str:
    """The plain-text part: the report as written, then the notes and the same footer."""
    lines = [mail.title, mail.date_label, ""]
    if mail.artifact_note:
        lines += [mail.artifact_note, ""]
    lines += [mail.markdown[:REPORT_MAX_CHARS].strip(), ""]
    lines += [*mail.notes, ""] if mail.notes else []
    lines += [
        "--",
        f"Sent by MAYA AI for {mail.account_email}, a schedule you set up.",
        "Pause or delete it in Settings > Schedules.",
    ]
    return "\n".join(lines) + "\n"
