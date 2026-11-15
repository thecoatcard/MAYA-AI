# app/schedules

Scheduled reports and tasks (ADR-0033). Status: implemented and tested offline with fakes; not run against live Gemini, SMTP, `schtasks` or voice.

| File | Role |
| :--- | :--- |
| `models.py` | Pydantic models; the JSON field names are the REST contract |
| `next_run.py` | Next occurrence (zoneinfo, DST-safe), occurrences between two moments |
| `validation.py` | Length caps, secret scan, recipients via `normalize_recipient`, days, depth, format |
| `store.py` | One JSON document in user settings (key `schedules`), compare-and-set, user index |
| `service.py` | CRUD, optimistic `version`, drafts (hash), updates feed, preview text |
| `runner.py` | The 30 s engine: due runs, catch-up (one run, 48 h limit), retry once, auto-pause after 3 |
| `safety.py` | Plan filter for unattended runs (research and, for a file format, document only) |
| `locks.py` | Redis `SET NX EX` lock with an in-process fallback |
| `task_probe.py` | Reads the run's task (status, report text, artifacts) |
| `messages.py`, `recipients.py` | System mails (report, recipient code) and the verification flow |
| `tools.py` | Chat and voice tools (`schedule_report`, `list_/pause_/resume_/delete_schedule`, `run_schedule_now`) |
| `factory.py`, `runtime.py` | Wiring for the lifespan; the current services for chat/voice |
| `run_due.py` | `python -m app.schedules.run_due`: waits for due work (wake-PC mode) |

Rules that must not be weakened: mail only through `SmtpEmailSender` (never `app/mail`); a draft becomes a schedule only through `confirm_draft` (REST click); a task whose idempotency key starts with `sched-` is never auto-approved; unattended runs keep only safe steps. Tests: `tests/unit/test_schedules_*.py`, `tests/support/schedule_fakes.py`.
