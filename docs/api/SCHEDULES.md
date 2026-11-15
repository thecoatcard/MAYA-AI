# Schedules API

Prefix `/api/v1/schedules`. Every route needs the signed-in user (bearer token) and acts on that user's own schedules only. Bodies and answers are JSON, field names snake_case. Errors are `{"error": "<code>", "message"?, "fields"?}`: `validation_error` (422), `not_found` (404), `schedule_limit` (409, at most 5), `version_conflict` (409), `already_running` (409), `rate_limited` (429), `scheduler_unavailable` (503), `service_unavailable` (503, system mailer missing), `invalid_or_expired_code` (400). Design: ADR-0033.

## Schedule

```json
{
  "id": "sch_ab12...", "name": "Tech news", "instructions": "...", "topic": "", "kind": "report",
  "cadence": {"type": "daily", "time": "08:00", "days": [], "timezone": "Asia/Kolkata"},
  "deliver": {"mail": true, "recipients": ["a@b.com"], "chat_artifact": true, "voice_on_open": true},
  "format": "chat", "depth": {"sources_target": 10, "use_deep_research": false},
  "enabled": true, "created_at": "...", "next_run_at": "...", "missed_policy": "run_once", "version": 1,
  "last_run": {"at": "...", "status": "completed", "task_id": "...", "summary": "<report Markdown>",
               "spoken_summary": "...", "announced": false, "artifact_ids": [],
               "mail_status": "sent", "reason": ""}
}
```

- `kind`: `report` | `task`. `cadence.type`: `daily` | `weekdays` | `weekly` (exactly one day) | `custom_days`. `time` is 24-hour `HH:MM` in `timezone` (IANA). `days` are `mon`..`sun`; the API also accepts full names and numbers 0 (Monday) to 6 (Sunday) and always returns the three-letter names. One run per day at most.
- `format`: `chat` (default) | `pdf` | `docx` | `pptx` | `xlsx` | `md`. A file is made only for a file format.
- `last_run.status`: `completed` | `failed` | `needs_attention` | `skipped`. `mail_status`: `sent`, `partial`, `not_requested`, `mail_not_configured`, `recipient_unverified`, `failed`. `reason` is plain text.
- Additive fields the Settings page may use: `consecutive_failures`, `paused_reason` (`""`, `user`, `failures`), `pending_run` (a run is in flight), `retry_at`.
- Limits: name 80, instructions 8 to 2000 characters (text that looks like a password or key is refused), 3 recipients, `sources_target` 1 to 20.

## Routes

| Route | Body | Answer |
| :--- | :--- | :--- |
| `GET ""` | | `{items: Schedule[], settings: {run_mode, voice_on_open, wake_registered, timezone}, account_email, verified_recipients[], mail_configured, max_schedules}` (the last four and `timezone` are additive) |
| `POST ""` | `{name, instructions, cadence, deliver, format, depth, topic?}` | `201` Schedule (same validation as the chat card) |
| `PATCH /{id}` | any of `enabled, name, instructions, topic, cadence, deliver, format, depth`, optional `version` | Schedule (`409 version_conflict` when `version` is stale) |
| `DELETE /{id}` | | `204` |
| `POST /{id}/run-now` | | `{task_id}` (`409 already_running`) |
| `GET /updates` | | `{items: [{schedule_id, name, run_at, status, summary, spoken_summary, format, artifact_ids, needs_attention}]}` unannounced runs (additive: `voice_on_open`, `reason`, `mail_status`) |
| `POST /updates/{schedule_id}/ack` | | `204` |
| `PUT /settings` | `{run_mode?: "app_open"\|"wake_pc", voice_on_open?, wake_registered?, timezone?}` | settings object |
| `POST /recipients/request` | `{email}` | `{sent: true}`; for an already verified address (the account's own too) `{sent: false, verified: true}`. Three codes per address and six per user per hour (`429`) |
| `POST /recipients/confirm` | `{email, code}` | `{verified: true}`; `400` for a wrong, expired or used code (5 tries, valid 10 minutes) |
| `GET /drafts/{id}` | | `{id, status, preview, schedule, created_at, expires_at, schedule_id}` |
| `POST /drafts/{id}/confirm` | | `201` Schedule, exactly what the card showed. Click-only: no model, tool or voice path calls it. Repeating it returns the same schedule |
| `POST /drafts/{id}/cancel` | | `204` |

`timezone` in `PUT /settings` is the device's IANA zone; the web app sends it so chat and voice can offer it as the default.

## Chat and voice

When a user asks for a recurring report, MAYA asks missing details with the normal question card and then emits a preview card:

- SSE `schedule.draft` (also stored on the assistant message as `metadata.schedule_drafts[]`, and `metadata.schedule_draft` = the first one as `{id, name, preview, schedule, expires_at}`): `{draft_id, name, preview, schedule, expires_at}`. `preview` reads like `Every day at 08:00 (Asia/Kolkata) · Tech news · mail to a@b.com · chat only · ~10 sources/run · est. 12 model calls/run`. Buttons: Create (`POST /drafts/{draft_id}/confirm`) and Cancel (`.../cancel`).
- Voice WebSocket frame `SCHEDULE_DRAFT` `{message_id, card}` with the same card. A spoken or typed "yes" never creates the schedule.
- `voice_on_open`: the web app starts master voice MUTED when `GET /updates` has items with `voice_on_open: true`; master voice speaks them and the backend marks them announced after the spoken turn (do not also call `ack` for those before they are spoken).

## Mail

Scheduled reports are mailed by MAYA's own system mailer (`MAYA_SMTP_*`), never by the user's mailbox. Each mail names the account it was set up for and says to pause or delete the schedule in Settings > Schedules. Without `MAYA_SMTP_*` the run still completes with `mail_status: "mail_not_configured"` and recipient codes cannot be sent (`503`).

## Status

Implemented and tested offline (fakes). Not run against live Gemini, SMTP or voice.
