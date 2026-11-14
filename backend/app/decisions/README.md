# app/decisions

- **Status:** Implemented locally, offline-tested only. The real service has NOT been called (see ADR-0029).
- **Owner:** Repository owner
- **Location:** `backend/app/decisions/`

The Gemini decision model (`gemini-decision-1`) answers yes/no, pick-one and score questions about a piece of text. MAYA uses it as an **advisory second opinion** in many places. Rules that never change:

1. **Fail open.** An error, timeout, safety block, malformed answer, missing key, open circuit, exhausted call budget or `MAYA_DECISIONS_ENABLED=false` returns "no opinion" and the previous behaviour runs unchanged.
2. **Safer only.** An answer may escalate a route, add a warning or an "unverified" note, flag a step as possibly incomplete, refuse a secret memory or trigger the existing repair round. It never approves anything, never lowers an approval tier and never replaces a deterministic check.
3. **Untrusted input.** Instructions are MAYA-written text; user, web, mail and tool text is wrapped with `untrusted_block` as data.
4. **No content in logs or metrics.** Only purpose, outcome and latency.

| Module | Job |
| :-- | :-- |
| `client.py` | httpx REST client (`POST /v1beta/decisions`, key in the `x-goog-api-key` header), key from the shared `KeyPool`, one retry on 429/5xx, strict response parsing |
| `questions.py` | `noul` / `choice` / `score` builders, explicit `other` option, `untrusted_block`, typed answers and threshold helpers |
| `service.py` | `DecisionService.decide`, circuit breaker (5 failures, 60 s), per-purpose and global per-minute budget, counters |
| `runtime.py` | process-wide handle; `decide` returns `None` with no service |
| `factory.py` | built in the app lifespan from settings |
| `screen.py` | prompt-injection screen (web, mail, MCP output) |
| `mail_triage.py` | urgency, category, needs-reply labels |
| `step_review.py` | task step review. Calibrated: a model objection is an advisory note; `flagged` needs deterministic evidence (placeholder, error marker, failed step check) AND `MAYA_STEP_REVIEW_MODE=enforce` to mark "finished with problems". Modes: `off`, `advisory` (default), `enforce`. Logs `step_review outcome=flagged\|noted\|clear\|timeout` |
| `doc_quality.py` | chapter / document / deck rubric; optional PDF `document` block (max 15 MB) |
| `approval_check.py` | payload-matches-request and untrusted-instruction warnings for approval cards |
| `answer_check.py` | "needs a web search" and "answer supported by sources" notes |
| `memory_check.py` | secret / one-off check before a memory is saved |
| `voice_digest.py` | is a step update worth interrupting for |
| `planner_hint.py` | advisory specialist-step hint for the planner |

Chat route tie-breaking lives in `app/services/chat_route_hint.py`.

Status: `GET /api/v1/decisions/status` (signed in; switches, circuit state, counters per purpose; no secrets). Settings: see `docs/guides/CONFIGURATION.md` (`MAYA_DECISIONS_*`).

Tests are offline with fake services: `tests/unit/test_decisions_*.py`.

## memory_gate (chat memory recall)

Purpose `memory_gate` (`app/decisions/memory_gate.py`), advisory and fail-open, 1.2 s timeout, on the chat hot path. `needs_memory` (noul) runs in parallel with the recall query: a clear no (probability of yes at or below 0.15) skips injection and the work log says "No memory needed". When memory is needed, one `score` question per recalled memory (levels unrelated .. needed) keeps those at "relevant" or above, at most 6, shown as "Using N saved memories". Standing preferences keep their deterministic path. Any error, timeout, open circuit or missing answer keeps today's behaviour (inject all recalled memories). Logs only the outcome and counts.
