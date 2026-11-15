"""'Create an agent for X' (English, Hinglish, Hindi) is recognised deterministically."""

from __future__ import annotations

import pytest

from app.agents.custom.intent import is_create_agent_request


@pytest.mark.parametrize(
    "text",
    [
        "Mere liye ek agent banao jo meri Python files ka code review kare",
        "create an agent that reviews my Python files",
        "Please make me a custom agent for weekly reports",
        "mere liye ek agent bana do jo emails summarise kare",
        "Build an assistant for invoices",
        "ek bot banao jo daily news bheje",
        "मेरे लिए एक एजेंट बनाओ जो कोड रिव्यू करे",
        "I need a new agent for SQL review",
    ],
)
def test_create_agent_requests_are_recognised(text: str) -> None:
    assert is_create_agent_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Write a Python script that reviews code",
        "run my reviewer agent on report.docx",
        "what is an agent in AI?",
        "summarise this document",
        "sales.csv mein total column jodo",
        "Make a 12 pages Java notes pdf",
        "",
    ],
)
def test_other_messages_are_not(text: str) -> None:
    assert is_create_agent_request(text) is False
