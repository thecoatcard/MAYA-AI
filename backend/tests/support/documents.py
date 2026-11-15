"""Shared fixtures for the themed document-generation tests."""

from __future__ import annotations

import pytest

from app.automation.browser_pdf import browser_available
from app.automation.model import Deck, Document

HINGLISH = "नमस्ते MAYA"

requires_browser = pytest.mark.skipif(
    not browser_available(),
    reason="No Microsoft Edge or Google Chrome found; real-browser PDF tests skipped",
)


def sample_document(theme: str = "executive", **overrides: object) -> Document:
    data: dict[str, object] = {
        "theme": theme,
        "title": "Quarterly Operations Report",
        "subtitle": f"Q3 2026 - {HINGLISH}",
        "author": "MAYA AI",
        "date": "2026-11-09",
        "blocks": [
            {"type": "heading", "level": 1, "text": "Executive summary"},
            {
                "type": "paragraph",
                "text": f"Plain **bold** *italic* `code` [site](https://example.com) {HINGLISH}",
            },
            {"type": "metrics", "items": [{"value": "98%", "label": "Success"}]},
            {"type": "callout", "kind": "warning", "text": "Check **this**."},
            {"type": "heading", "level": 2, "text": "Numbers"},
            {
                "type": "table",
                "headers": ["Segment", "Revenue"],
                "rows": [["Enterprise", "$720K"], ["SMB", "$310K"]],
                "caption": "Table 1",
            },
            {"type": "bullets", "items": ["one", "two"]},
            {"type": "numbered", "items": ["first", "second"]},
            {"type": "quote", "text": "Ship it.", "by": "Team"},
            {"type": "code", "language": "python", "text": "print('hi')"},
            {"type": "page_break"},
            {"type": "heading", "level": 1, "text": "Appendix"},
            {"type": "paragraph", "text": "Closing words."},
        ],
    }
    data.update(overrides)
    return Document.model_validate(data)


def sample_deck(theme: str = "executive") -> Deck:
    return Deck.model_validate(
        {
            "theme": theme,
            "title": "Board Review",
            "slides": [
                {
                    "layout": "title",
                    "title": "MAYA AI Board Review",
                    "subtitle": f"Q3 - {HINGLISH}",
                    "notes": "Open with the vision.",
                },
                {"layout": "section", "title": "Where we are"},
                {
                    "layout": "bullets",
                    "title": "Highlights",
                    "bullets": ["Shipped **voice** onboarding", "Latency halved", HINGLISH],
                    "notes": "Talk about [roadmap](https://example.com).",
                },
                {
                    "layout": "two_column",
                    "title": "Wins and risks",
                    "left": {"heading": "Wins", "bullets": ["Success 98%", "No incidents"]},
                    "right": {"heading": "Risks", "bullets": ["Browser needed", "Quota"]},
                },
                {
                    "layout": "cards",
                    "title": "Pillars",
                    "cards": [
                        {"title": "Agents", "body": "Specialists coordinate."},
                        {"title": "Verified", "body": "Every file is checked."},
                        {"title": "Secure", "body": "Row-level security."},
                    ],
                },
                {
                    "layout": "metrics",
                    "title": "Numbers",
                    "metrics": [
                        {"value": "98%", "label": "Success"},
                        {"value": "$1.2M", "label": "ARR"},
                    ],
                },
                {"layout": "quote", "title": "Voice", "quote": "It just works.", "by": "Pilot"},
                {
                    "layout": "table",
                    "title": "Revenue",
                    "table": {"headers": ["Segment", "Revenue"], "rows": [["SMB", "$310K"]]},
                },
                {
                    "layout": "timeline",
                    "title": "Roadmap",
                    "steps": [
                        {"title": "Q4", "detail": "Desktop GA"},
                        {"title": "Q1", "detail": "Connectors"},
                        {"title": "Q2", "detail": "Teams"},
                    ],
                },
                {
                    "layout": "image",
                    "title": "Diagram",
                    "image": {
                        "src": TINY_PNG,
                        "alt": "A single pixel",
                        "caption": "Figure 1",
                    },
                },
                {
                    "layout": "comparison",
                    "title": "Before and after",
                    "left": {"heading": "Before", "bullets": ["Manual reports", "Two days"]},
                    "right": {"heading": "After", "bullets": ["Automatic reports", "Minutes"]},
                },
                {
                    "layout": "code",
                    "title": "Sample call",
                    "code": {"language": "python", "text": "def greet(name):\n    return name"},
                },
                {
                    "layout": "closing",
                    "title": "Thank you",
                    "subtitle": "Questions?",
                    "bullets": ["hello@example.com"],
                },
                # Slide theme library layouts (added after the original thirteen).
                {"layout": "hero_cover", "title": "Launch", "subtitle": "Plan"},
                {"layout": "section_break", "title": "Part two", "number": "02"},
                {
                    "layout": "stat_grid",
                    "title": "Tiles",
                    "metrics": [
                        {"value": "98%", "label": "Success", "progress": 98},
                        {"value": "12", "label": "Teams"},
                    ],
                },
                {
                    "layout": "icon_cards",
                    "title": "Icons",
                    "cards": [
                        {"title": "Fast", "body": "Quick.", "icon": "rocket"},
                        {"title": "Safe", "body": "Checked.", "icon": "no-such-icon"},
                    ],
                },
                {
                    "layout": "process",
                    "title": "Steps",
                    "steps": [{"title": "Plan", "detail": "d"}, {"title": "Ship"}],
                },
                {
                    "layout": "image_text",
                    "title": "Picture and words",
                    "image": {"src": TINY_PNG, "alt": "pixel"},
                    "bullets": ["one", "two"],
                },
                {
                    "layout": "chart",
                    "title": "Chart",
                    "chart": {
                        "type": "column",
                        "categories": ["A", "B"],
                        "series": [{"name": "S", "values": [1, 2]}],
                    },
                },
            ],
        }
    )


# A valid 1x1 PNG.
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
