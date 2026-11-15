# ADR-0029: Gemini decision model as an advisory, fail-open second opinion

- Status: Proposed
- Date: 2026-11-13

## Context

The owner asked to use `gemini-decision-1` wherever it helps MAYA make fewer errors. The model answers yes/no, pick-one and score questions about supplied content with probabilities, through its own REST endpoint (`POST /v1beta/decisions`). From Google's documentation it takes text and media, has a 262,144 token limit, accepts text-only instructions, has no multi-turn, tools or caching, and always spreads probability across the options of a pick-one question. The owner approved this external-service integration.

## Decision

1. New package `backend/app/decisions/` with an httpx client that takes its key from the existing Gemini `KeyPool` (rotation, cooldown and failover are reused; keys are sent only in a header and never logged), a service with a circuit breaker and per-purpose budgets, and typed answers.
2. **Fail-open rule.** Any error, timeout (3 s interactive, 8 s background), safety block, malformed response, missing key, open circuit, exhausted budget or `MAYA_DECISIONS_ENABLED=false` means "no opinion", and the previous behaviour runs unchanged.
3. **Never loosens approvals.** A decision may only escalate or warn: choose a safer route (approval, task), add an "unverified" or warning note, flag a step as possibly incomplete (the task then finishes "with problems"), refuse a secret memory, force a Notion auto-approve back to manual on a strong flag, or trigger the existing single repair/expansion round. It never approves, never lowers a tier and never replaces a deterministic check. A pick-one answer needs a probability threshold and is ignored when it picks `other`.
4. Content is untrusted: instructions are written by MAYA and user, web, mail and tool text is wrapped as data. Logs and metrics carry only purpose, outcome and latency.

### Where it is used

Chat route tie-breaker (only for ambiguous routes and never over a user-stated count or format); "needs a web search" and "answer supported by sources" notes; task step review; chapter and document quality review (a PDF that already exists, up to 15 MB, is passed for a visual score); approval payload checks; prompt-injection screen for web, mail and MCP text; mail triage labels; voice step-update filtering; memory secret check; planner hint. Not done: a log PII hook (risk of sending sensitive text for little gain), a choice-question backup for standing-preference detection (the regex stays).

## Consequences

- Cost estimate: $0.10 per 1M input tokens. A call is about 300 to 4,000 input tokens, so a normal chat turn adds well under $0.0005 and a long document review under $0.003. The call budget is 60 per minute by default (the free tier allows 200).
- A new registry alias `decision` (registry-only, not callable through the generate methods).
- A flagged task step or document adds a visible, advisory note; false positives are possible and only cost a note.

## Unverified claims

No live call has been made (DNS to the service fails on the owner's machine). The request and response shapes, error codes for safety blocks, the quoted latency (150 to 250 ms), the probability calibration and the confidence semantics come from documentation and are unverified. Thresholds are conservative guesses and need tuning with real data. The client validates every response strictly and treats anything unexpected as "no opinion".
