"""Prompt for an unattended scheduled report (ADR-0033).

The text becomes the instruction of an ordinary task. It names a file format only when the user
asked for one (``app.services.deliverable_intent`` reads the words), so a chat-only schedule never
plans a file. Real facts only: a report with few or no readable sources says so.
"""

from __future__ import annotations

from datetime import date
from typing import Final

FORMAT_WORDS: Final = {
    "pdf": "a PDF",
    "docx": "a Word document (docx)",
    "pptx": "a slide deck (pptx)",
    "xlsx": "an Excel sheet (xlsx)",
    "md": "a Markdown file (.md)",
}

REPORT_STRUCTURE: Final = (
    "Structure of the report (headings marked with # signs, bullets with dashes):\n"
    "1. A title line: '# <report name> - <weekday, day month year>'.\n"
    "2. '## Top stories': one '### <headline>' per story, then 1-2 plain sentences saying what "
    "happened, then a line 'Why it matters: ...'. Add a source link '[Publisher](URL)' ONLY for a "
    "page that was really read in this run; never write a URL from memory.\n"
    "3. '## Key takeaways': 3-5 short bullet points.\n"
    "4. '## Limits': only when fewer sources than planned could be read or a topic part had no "
    "reliable source. Say exactly what is missing.\n"
    "Rules: real facts only; never invent a story, number, quote, date or link. If no source "
    "could be read, write one short paragraph saying so instead of filling the report. Plain, "
    "specific language; no filler, no emojis."
)

UNATTENDED_RULES: Final = (
    "This run is unattended and scheduled by the user. Use only reading and writing-the-report "
    "steps (web search and the report itself). Do not write to any outside service or "
    "workspace, and do not ask the user anything: if a detail is missing, use the most reasonable "
    "reading of the instructions and say what you assumed in 'Limits'."
)


def build_instruction(
    *,
    name: str,
    instructions: str,
    topic: str,
    day: date,
    timezone: str,
    file_format: str,
    sources_target: int,
) -> str:
    """The instruction of one run. ``file_format`` is ``chat`` or a key of ``FORMAT_WORDS``."""
    today = f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}"
    subject = topic.strip() or name.strip()
    if file_format == "chat":
        output = (
            "Give the finished report as the answer text of this task, in full. Reply as text only."
        )
    else:
        output = (
            f"Produce the finished report as {FORMAT_WORDS[file_format]}, with the same "
            "structure, and keep the full report text as the task answer too."
        )
    return (
        f"Scheduled report '{name}' for {today} ({timezone}).\n"
        f"Topic: {subject}\n"
        f"What the user asked for: {instructions.strip()}\n"
        f"Use today's date ({today}) for everything current. Read about {sources_target} "
        "different, reliable sources.\n"
        f"{REPORT_STRUCTURE}\n{UNATTENDED_RULES}\n{output}"
    )


def spoken_summary(markdown: str, limit: int = 900) -> str:
    """Short, speakable key points from the report: no URLs, no Markdown marks."""
    import re

    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"https?://\S+", "", text)
    headlines: list[str] = []
    takeaways: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
        elif line.startswith("### ") and "top" in section:
            headlines.append(line[4:].strip(" *#"))
        elif section.startswith("key takeaways") and re.match(r"^[-*•]\s+", line):
            takeaways.append(re.sub(r"^[-*•]\s+", "", line).strip(" *"))
    parts: list[str] = []
    if headlines:
        parts.append("Top stories: " + "; ".join(headlines[:5]) + ".")
    if takeaways:
        parts.append("Key takeaways: " + " ".join(t.rstrip(".") + "." for t in takeaways[:3]))
    if not parts:
        first = next((s.strip(" #*-") for s in text.splitlines() if s.strip()), "")
        parts.append(first)
    spoken = " ".join(parts).strip()
    return spoken[:limit].rsplit(" ", 1)[0] if len(spoken) > limit else spoken
