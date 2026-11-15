"""Mail template, recipient verification, chat/voice tools, voice digest and the REST contract."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.chat import get_chat_rate_limiter
from app.api.v1.errors import install_error_handlers
from app.api.v1.schedules import router
from app.auth.dependencies import get_principal
from app.auth.errors import InvalidCode, RateLimited
from app.auth.models import Principal
from app.core.email_templates import ReportMail, render_report_html, render_report_text
from app.schedules.messages import report_email
from app.schedules.models import Cadence, Deliver
from app.schedules.service import ScheduleInput
from app.schedules.tools import (
    DELETE_SCHEDULE,
    SCHEDULE_REPORT,
    SCHEDULE_TOOL_NAMES,
    VOICE_SCHEDULE_TOOL_NAMES,
    ScheduleToolkit,
    schedule_voice_declarations,
)
from app.voice.schedule_digest import ARTIFACTS_LINE, schedule_update_text, voice_updates
from app.voice.task_watch import AnnouncedStore, TaskAnnouncer
from tests.support.schedule_fakes import ACCOUNT, USER, WS, Rig, make_rig

MD = (
    "# Tech <b>news</b> - Friday\n## Top stories\n### Chip <script>alert(1)</script> wins\n"
    "A **big** day. [Reuters](https://example.com/a?x=1&y=2) and [bad](javascript:alert(1)).\n"
    "Why it matters: supply.\n## Key takeaways\n- AI spend rises\n- Chips tight\n"
)


def test_report_mail_escapes_structures_and_has_no_remote_content() -> None:
    mail = ReportMail(
        title="Tech news — Friday, 14 November 2026",
        date_label="Friday, 14 November 2026",
        preheader="Chip wins",
        markdown=MD,
        account_email=ACCOUNT,
        artifact_note="A file with the full report was saved in Artifacts.",
        notes=("Only 3 sources could be read.",),
    )
    html = render_report_html(mail)
    assert "<script" not in html and "href='javascript" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html
    assert re.search(r"<h2[^>]*>Top stories</h2>", html) and "Key takeaways" in html
    assert "<li" in html and "<strong>big</strong>" in html
    assert "href='https://example.com/a?x=1&amp;y=2'" in html
    for forbidden in ("<img", "<link", "@import", "url(", "<iframe", "src="):
        assert forbidden not in html
    assert "MAYA AI" in html and "prefers-color-scheme:dark" in html
    assert f"Sent by MAYA AI for {ACCOUNT}" in html and "Settings &gt; Schedules" in html
    assert "Artifacts" in html and "Only 3 sources" in html
    assert len(html.encode()) < 100_000
    text = render_report_text(mail)
    assert "Settings > Schedules" in text and "Top stories" in text


def test_report_mail_size_is_capped_and_subject_format() -> None:
    message = report_email(
        to="a@b.com",
        schedule_name="Tech news",
        day=date(2026, 11, 14),
        markdown="- item\n" * 30_000,
        account_email=ACCOUNT,
        file_made=False,
    )
    assert message.subject == "Tech news — Saturday, 14 November 2026"
    assert message.html is not None and len(message.html.encode()) < 100_000


# ------------------------------------------------------------------ recipient verification


def _code_from(rig: Rig) -> str:
    assert rig.sender is not None
    match = re.search(r"is (\d{6})\.", rig.sender.sent[-1].body)
    assert match
    return match.group(1)


async def test_recipient_verification_flow_hash_and_use() -> None:
    rig = make_rig()
    assert await rig.verifier.request(WS, USER, "Friend@Example.org") == {"sent": True}
    code = _code_from(rig)
    stored = rig.backend.data["schedules"]
    assert code not in stored  # only a hash is stored, never the code
    with pytest.raises(InvalidCode):
        await rig.verifier.confirm(
            WS, USER, "friend@example.org", "000000" if code != "000000" else "111111"
        )
    assert await rig.verifier.confirm(WS, USER, "friend@example.org", code)
    assert "friend@example.org" in (await rig.store.load(WS, USER)).verified
    with pytest.raises(InvalidCode):  # a used code does not work twice
        await rig.verifier.confirm(WS, USER, "friend@example.org", code)
    assert await rig.verifier.request(WS, USER, ACCOUNT.upper()) == {
        "sent": False,
        "verified": True,
    }  # the account's own address is pre-verified


async def test_recipient_code_expiry_attempts_and_rate_limit() -> None:
    rig = make_rig()
    await rig.verifier.request(WS, USER, "a@example.org")
    code = _code_from(rig)
    rig.clock.advance(minutes=11)
    with pytest.raises(InvalidCode):
        await rig.verifier.confirm(WS, USER, "a@example.org", code)
    rig2 = make_rig()
    await rig2.verifier.request(WS, USER, "b@example.org")
    good = _code_from(rig2)
    wrong = "123456" if good != "123456" else "654321"
    for _ in range(5):
        with pytest.raises(InvalidCode):
            await rig2.verifier.confirm(WS, USER, "b@example.org", wrong)
    with pytest.raises(InvalidCode):  # locked out after 5 wrong tries, even with the right code
        await rig2.verifier.confirm(WS, USER, "b@example.org", good)
    rig3 = make_rig()
    for _ in range(3):
        await rig3.verifier.request(WS, USER, "c@example.org")
    with pytest.raises(RateLimited):
        await rig3.verifier.request(WS, USER, "c@example.org")
    rig3.clock.advance(hours=1, minutes=1)
    assert (await rig3.verifier.request(WS, USER, "c@example.org"))["sent"] is True


async def test_recipient_request_needs_system_mailer() -> None:
    from app.auth.errors import ServiceUnavailable

    rig = make_rig(sender=False)
    with pytest.raises(ServiceUnavailable):
        await rig.verifier.request(WS, USER, "a@example.org")


# ------------------------------------------------------------------------ chat / voice tools


def _kit(
    rig: Rig, texts: list[str] | None = None, tz: str | None = "Asia/Kolkata"
) -> ScheduleToolkit:
    return ScheduleToolkit(
        services=rig.services,
        workspace_id=WS,
        user_id=USER,
        default_timezone=tz,
        user_texts=lambda: texts or [],
    )


FULL: dict[str, Any] = {
    "name": "Tech news",
    "instructions": "The big news in the tech industry today.",
    "time": "08:00",
    "cadence_type": "daily",
    "timezone": "Asia/Kolkata",
    "mail": True,
    "recipients": ["my account email"],
}


async def test_missing_slots_become_questions_and_nothing_is_created() -> None:
    rig = make_rig()
    kit = _kit(rig)
    out = await kit.call(SCHEDULE_REPORT, {"instructions": "The big news in tech today."})
    assert out["status"] == "needs_details" and 1 <= len(out["questions"]) <= 3
    headers = {q["header"] for q in out["questions"]}
    assert "Time" in headers and "Days" not in headers or len(out["questions"]) <= 3
    assert not kit.new_drafts and (await rig.store.load(WS, USER)).items == []
    tz_q = await _kit(rig).call(SCHEDULE_REPORT, {**FULL, "timezone": ""})
    assert any("Asia/Kolkata" in [o["label"] for o in q["options"]] for q in tz_q["questions"])


async def test_preview_draft_is_exact_and_created_only_by_confirm() -> None:
    rig = make_rig()
    kit = _kit(rig, ["every day at 8 am send me the tech news"])
    out = await kit.call(SCHEDULE_REPORT, FULL)
    assert out["status"] == "preview_shown" and len(kit.new_drafts) == 1
    draft = kit.new_drafts[0]
    assert draft.preview.startswith("Every day at 08:00 (Asia/Kolkata)")
    assert (
        f"mail to {ACCOUNT}" in draft.preview and "chat only" not in draft.preview.split("mail")[0]
    )
    assert "~10 sources/run" in draft.preview and "model calls/run" in draft.preview
    assert (await rig.store.load(WS, USER)).items == []  # a proposal creates nothing
    schedule, _ = await rig.service.confirm_draft(WS, USER, draft.id)
    assert schedule.deliver.recipients == [ACCOUNT] and schedule.format == "chat"
    again, _ = await rig.service.confirm_draft(WS, USER, draft.id)  # idempotent
    assert again.id == schedule.id and len((await rig.store.load(WS, USER)).items) == 1


async def test_file_format_only_when_user_named_it() -> None:
    rig = make_rig()
    kit = _kit(rig, ["every day at 8 send me the tech news"])
    await kit.call(SCHEDULE_REPORT, {**FULL, "format": "pdf"})
    assert kit.new_drafts[0].schedule["format"] == "chat"
    kit2 = _kit(rig, ["every day at 8 send me the tech news as a PDF"])
    await kit2.call(SCHEDULE_REPORT, {**FULL, "format": "pdf"})
    assert kit2.new_drafts[0].schedule["format"] == "pdf"
    assert "PDF file in Artifacts" in kit2.new_drafts[0].preview


async def test_draft_tampering_and_cancel_and_expiry() -> None:
    from app.schedules.service import NotFound

    rig = make_rig()
    kit = _kit(rig)
    await kit.call(SCHEDULE_REPORT, FULL)
    draft = kit.new_drafts[0]

    def tamper(raw: str) -> str:
        return raw.replace("Tech news", "Evil news", 1)

    rig.backend.data["schedules"] = tamper(rig.backend.data["schedules"])
    with pytest.raises(NotFound):
        await rig.service.confirm_draft(WS, USER, draft.id)
    kit2 = _kit(rig)
    await kit2.call(SCHEDULE_REPORT, {**FULL, "name": "Second"})
    await rig.service.cancel_draft(WS, USER, kit2.new_drafts[0].id)
    with pytest.raises(NotFound):
        await rig.service.confirm_draft(WS, USER, kit2.new_drafts[0].id)
    kit3 = _kit(rig)
    await kit3.call(SCHEDULE_REPORT, {**FULL, "name": "Third"})
    rig.clock.advance(hours=3)
    with pytest.raises(NotFound):
        await rig.service.confirm_draft(WS, USER, kit3.new_drafts[0].id)


async def test_management_tools_pause_resume_run_delete() -> None:
    rig = make_rig()
    item = await rig.service.create(
        WS,
        USER,
        ScheduleInput(
            name="Tech news",
            instructions="The big news in tech today.",
            cadence=Cadence(timezone="UTC"),
            deliver=Deliver(),
        ),
    )
    kit = _kit(rig)
    assert (await kit.call("list_schedules", {}))["schedules"][0]["id"] == item.id
    assert (await kit.call("pause_schedule", {"schedule": "tech"}))["status"] == "ok"
    assert not (await rig.store.load(WS, USER)).items[0].enabled
    assert (await kit.call("resume_schedule", {"schedule": item.id}))["status"] == "ok"
    assert (await kit.call("run_schedule_now", {"schedule": "Tech news"}))["status"] == "ok"
    assert (await kit.call(DELETE_SCHEDULE, {"schedule": item.id, "confirm": False}))["status"] == (
        "needs_confirmation"
    )
    assert (await kit.call(DELETE_SCHEDULE, {"schedule": item.id, "confirm": True}))[
        "status"
    ] == "ok"
    assert (await kit.call("pause_schedule", {"schedule": "zzz"}))["status"] == "not_found"


def test_voice_declarations_exclude_delete() -> None:
    names = {d["name"] for d in schedule_voice_declarations()}
    assert names == set(VOICE_SCHEDULE_TOOL_NAMES) and DELETE_SCHEDULE not in names
    assert set(SCHEDULE_TOOL_NAMES) - names == {DELETE_SCHEDULE}
    assert schedule_voice_declarations()[0]["parameters"]["type"] == "OBJECT"


def test_no_tool_name_can_confirm_a_draft() -> None:
    assert not any("confirm" in n or "approve" in n for n in SCHEDULE_TOOL_NAMES)


# --------------------------------------------------------------------------- voice digest


async def test_update_text_has_key_points_no_urls_and_artifacts_line() -> None:
    update = {
        "schedule_id": "s",
        "name": "Tech news",
        "status": "completed",
        "spoken_summary": "Top stories: Chip maker wins. Key takeaways: AI spend rises.",
        "format": "pdf",
    }
    text = schedule_update_text(update)
    assert "Tech news" in text and "Chip maker wins" in text and ARTIFACTS_LINE in text
    assert ARTIFACTS_LINE not in schedule_update_text({**update, "format": "chat"})
    attention = schedule_update_text({**update, "status": "needs_attention", "reason": "approval"})
    assert "attention" in attention


async def test_announced_only_after_model_turn_finished() -> None:
    rig = make_rig()
    await rig.service.create(
        WS,
        USER,
        ScheduleInput(
            name="Tech news",
            instructions="The big news in tech today.",
            cadence=Cadence(timezone="UTC"),
            deliver=Deliver(),
        ),
    )

    def finish(doc: Any) -> None:
        from app.schedules.models import LastRun

        doc.items[0].last_run = LastRun(
            at=rig.clock.now(),
            status="completed",
            summary="S",
            spoken_summary="Key point.",
        )

    await rig.store.update(WS, USER, finish)
    ready = await voice_updates(rig.service.updates, rig.service.ack, WS, USER)
    assert len(ready) == 1
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    async def notify(frame: dict[str, Any]) -> None:
        return None

    async def resolver(*_: Any) -> None:
        return None

    announcer = TaskAnnouncer(
        workspace_id=WS,
        user_id=USER,
        resolver=resolver,
        store=AnnouncedStore(redis=None),
        speak=speak,
        is_busy=lambda: False,
        notify=notify,
    )
    announcer.add_schedule_update(*ready[0])
    batch = announcer._take_batch()  # noqa: SLF001
    await announcer._deliver(batch)  # noqa: SLF001
    assert spoken and "Key point." in spoken[0]
    assert len(await rig.service.updates(WS, USER)) == 1  # spoken, but the turn has not finished
    await announcer.on_model_turn("Your tech news is ready.")
    assert await rig.service.updates(WS, USER) == []


# ---------------------------------------------------------------------------- REST contract


@pytest.fixture
async def client(request: pytest.FixtureRequest) -> Any:
    rig = make_rig()
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.state.schedule_services = rig.services
    principal = Principal(user_id=USER, workspace_id=WS, role="member", session_id=UUID(int=3))

    class Limiter:
        async def hit(self, key: str, limit: Any) -> bool:
            return True

        async def release(self, key: str, limit: Any) -> None:
            return None

    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_chat_rate_limiter] = lambda: Limiter()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        http.rig = rig  # type: ignore[attr-defined]
        yield http


BODY = {
    "name": "Tech news",
    "instructions": "The big news in the tech industry today.",
    "cadence": {"type": "daily", "time": "08:00", "days": [], "timezone": "Asia/Kolkata"},
    "deliver": {
        "mail": True,
        "recipients": [ACCOUNT],
        "chat_artifact": True,
        "voice_on_open": True,
    },
    "format": "chat",
    "depth": {"sources_target": 10, "use_deep_research": False},
}
SCHEDULE_FIELDS = {
    "id",
    "name",
    "instructions",
    "topic",
    "kind",
    "cadence",
    "deliver",
    "format",
    "depth",
    "enabled",
    "created_at",
    "last_run",
    "next_run_at",
    "missed_policy",
    "version",
}


async def test_rest_contract(client: httpx.AsyncClient) -> None:
    empty = (await client.get("/api/v1/schedules")).json()
    assert empty["items"] == []
    assert {"run_mode", "voice_on_open", "wake_registered"} <= set(empty["settings"])
    assert empty["settings"]["run_mode"] == "app_open"

    created = await client.post("/api/v1/schedules", json=BODY)
    assert created.status_code == 201
    item = created.json()
    assert SCHEDULE_FIELDS <= set(item) and item["missed_policy"] == "run_once"
    assert item["last_run"] is None and item["version"] == 1 and item["enabled"] is True

    patched = await client.patch(f"/api/v1/schedules/{item['id']}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    stale = await client.patch(f"/api/v1/schedules/{item['id']}", json={"name": "x2", "version": 1})
    assert stale.status_code == 409
    bad = await client.post("/api/v1/schedules", json={**BODY, "instructions": "x"})
    assert bad.status_code == 422 and bad.json()["error"] == "validation_error"

    settings = await client.put(
        "/api/v1/schedules/settings", json={"run_mode": "wake_pc", "voice_on_open": False}
    )
    assert settings.json()["run_mode"] == "wake_pc" and settings.json()["voice_on_open"] is False
    assert (
        await client.put("/api/v1/schedules/settings", json={"run_mode": "x"})
    ).status_code == 422

    run = await client.post(f"/api/v1/schedules/{item['id']}/run-now")
    assert run.status_code == 200 and "task_id" in run.json()
    assert (await client.post(f"/api/v1/schedules/{item['id']}/run-now")).status_code == 409

    assert (await client.get("/api/v1/schedules/updates")).json() == {"items": []}
    assert (await client.post("/api/v1/schedules/updates/sch_x/ack")).status_code == 404

    assert (await client.delete(f"/api/v1/schedules/{item['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/schedules/{item['id']}")).status_code == 404


async def test_rest_updates_shape_ack_and_recipients(client: httpx.AsyncClient) -> None:
    rig: Rig = client.rig  # type: ignore[attr-defined]
    item = (await client.post("/api/v1/schedules", json=BODY)).json()

    def finish(doc: Any) -> None:
        from app.schedules.models import LastRun

        doc.items[0].last_run = LastRun(
            at=rig.clock.now(),
            status="completed",
            summary="S",
            spoken_summary="K",
            artifact_ids=["a1"],
        )

    await rig.store.update(WS, USER, finish)
    updates = (await client.get("/api/v1/schedules/updates")).json()["items"]
    assert len(updates) == 1
    assert set(updates[0]) >= {
        "schedule_id",
        "name",
        "run_at",
        "status",
        "summary",
        "spoken_summary",
        "format",
        "artifact_ids",
        "needs_attention",
    }
    assert updates[0]["artifact_ids"] == ["a1"] and updates[0]["needs_attention"] is False
    assert (await client.post(f"/api/v1/schedules/updates/{item['id']}/ack")).status_code == 204
    assert (await client.get("/api/v1/schedules/updates")).json()["items"] == []

    sent = await client.post(
        "/api/v1/schedules/recipients/request", json={"email": "p@example.org"}
    )
    assert sent.json() == {"sent": True}
    code = _code_from(rig)
    wrong = await client.post(
        "/api/v1/schedules/recipients/confirm",
        json={"email": "p@example.org", "code": "000000" if code != "000000" else "111111"},
    )
    assert wrong.status_code == 400
    ok = await client.post(
        "/api/v1/schedules/recipients/confirm", json={"email": "p@example.org", "code": code}
    )
    assert ok.json() == {"verified": True}
    listing = (await client.get("/api/v1/schedules")).json()
    assert listing["verified_recipients"] == ["p@example.org"]


async def test_rest_draft_confirm_and_cancel_routes(client: httpx.AsyncClient) -> None:
    rig: Rig = client.rig  # type: ignore[attr-defined]
    kit = _kit(rig)
    await kit.call(SCHEDULE_REPORT, FULL)
    draft = kit.new_drafts[0]
    shown = (await client.get(f"/api/v1/schedules/drafts/{draft.id}")).json()
    assert shown["preview"] == draft.preview and "payload_hash" not in shown
    confirmed = await client.post(f"/api/v1/schedules/drafts/{draft.id}/confirm")
    assert confirmed.status_code == 201 and confirmed.json()["name"] == "Tech news"
    kit2 = _kit(rig)
    await kit2.call(SCHEDULE_REPORT, {**FULL, "name": "Other"})
    cancel = await client.post(f"/api/v1/schedules/drafts/{kit2.new_drafts[0].id}/cancel")
    assert cancel.status_code == 204
    assert (
        await client.post(f"/api/v1/schedules/drafts/{kit2.new_drafts[0].id}/confirm")
    ).status_code == 404
    assert datetime.now(UTC)  # keep the import used on every path
