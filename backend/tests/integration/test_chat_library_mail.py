"""'Email this message' (``source_message_id``): the body comes from that message as it is; the
recipient still has to be written by the user. Nothing is sent: the step waits for approval."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from tests.integration.test_chat_inline_files import IdempotentSubmitter, call
from tests.integration.test_chat_library import NOTES
from tests.integration.test_chat_mail import mail_world
from tests.integration.test_chat_phase2_tools import wire
from tests.integration.test_chat_streaming import World, events, send, sign_in
from tests.support.mail_fakes import FakeMailServer, install, standard_mailbox


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMailServer]:
    yield install(monkeypatch, standard_mailbox())


@pytest.fixture
async def world(tmp_path: Path, mailbox: FakeMailServer) -> AsyncIterator[World]:
    async with mail_world(tmp_path, chat_tool_loop=True) as value:
        yield value


async def test_the_body_is_the_message_and_the_recipient_is_the_users(
    world: World, mailbox: FakeMailServer
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": NOTES}]]
    first = events(await send(world, account, "explain java streams"))
    session_id = first[0].data["session_id"]
    notes_id = first[0].data["assistant_message_id"]
    world.models.queued_stream_scripts = [
        call(
            "send_email",
            {"to": "boss@example.com", "subject": "", "body": "the model's own words"},
        ),
        [{"text": "Prepared for your approval."}],
    ]
    events(
        await send(
            world,
            account,
            "email this to boss@example.com",
            session_id=session_id,
            source_message_id=notes_id,
        )
    )
    (task,) = submitter.submitted
    (step,) = task["steps"]
    assert step.tool == "mail_send" and step.tool_args["to"] == ["boss@example.com"]
    assert step.tool_args["body"] == NOTES.strip()  # the message as it is, not the model's words
    assert step.tool_args["subject"] == "Java Streams"
    assert mailbox.sent == []  # approval is a click; nothing was sent


async def test_without_a_source_the_models_body_is_kept(
    world: World, mailbox: FakeMailServer
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("send_email", {"to": "boss@example.com", "subject": "Hi", "body": "Short note."}),
        [{"text": "Prepared."}],
    ]
    events(await send(world, account, "mail boss@example.com: short note"))
    (task,) = submitter.submitted
    assert task["steps"][0].tool_args["body"] == "Short note."
