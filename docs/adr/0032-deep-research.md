# ADR-0032: Deep research mode

- Status: Proposed
- Date: 2026-11-13

## Context

Chat web search reads a handful of pages for one answer. The owner wants a mode (the composer brain toggle, `mode: "deep_research"` on `POST /api/v1/chat/message`) in which MAYA researches widely, analyses what it read, and then does what the user asked (a cited answer, a PDF, a DOCX, a deck, a Notion page). Approved defaults: at least 20 distinct sites, at most 40 pages fetched (the user may ask for more, up to 60), 10 minutes, about 60 model calls, paywalled, login and bot-blocked pages skipped and reported, the native search stack (Brave and Tavily only if their keys exist).

## Decision

1. **A task, not a chat turn.** With the mode set, the chat layer skips model-decided routing, runs the required-details gate (`required_details`; a message with no topic gets the question card, never a guess), and submits one task through the existing submission path. The reply is written by code (`research/intent.py: start_text`): it promises effort (sites, minutes) and never results; the model is not called in that turn. Without the mode nothing changes.
2. **Plan reuse.** The normal planner still decides what is asked. `research/plan.py` marks its research step with `input_data["deep_research"]` (or adds one in front when the planner made none). The research agent runs `DeepResearchRuntime.run` for such a step (budget: time cap plus 120 s); later steps (document, deck, Notion) read its cited report as their dependency output, so the existing document pipelines produce the files. No new agent role or database enum.
3. **Pipeline** (`backend/app/research/`): plan (6 to 10 sub-questions, 3 to 5 queries each, one structured call, a code fallback) -> wide search (queries interleaved across questions, about 80 candidates, 3 at a time, the per-minute search limit is waited out) -> select (canonical-URL dedupe, per-site cap of 2, best page per site first, heuristic score: authority, on-topic, recency, repeated hits, spam and thin pages dropped) -> fetch and extract (`fetcher.py` on top of `search/fetch.py`: SSRF-safe, redirects re-checked at most 3, robots.txt, 2 MB, 10 s connect and 20 s read, concurrency 6, per-site pacing, HTML and text-layer PDF only, one attempt per page) -> map (cheap model, per-page notes; quotes must appear verbatim in the page and be at most 25 words, figures must appear in the page, otherwise dropped) -> reduce (bigger model: findings with source numbers, contradictions, gaps, follow-up queries) -> at most one more search round for gaps (2 rounds in total) -> write (bigger model, body only) -> verify -> deliver.
4. **Honesty by code.** The status line (`Read N sources from M sites (K skipped)`), the skip reasons, the evidence table (claim -> sources, support by number of distinct sites), the confidence and limits section, the contradictions and the numbered source list are built by code from what was actually read. If fewer than the minimum sites were reachable the result says exactly how many and why; nothing pads it. Verification: a claim sentence without a citation to a source that was read is marked `[inference: no source cited]`; a citation to an unread source is removed (and counted); a number that the cited sources do not contain is flagged; the decision-service `grounding` check adds a visible note when it clearly finds unsupported claims. Nothing is silently kept or deleted.
5. **Safety.** Page text is untrusted data: sanitized, wrapped in the untrusted-data markers, regex-scanned and screened by the decision model. A page that tries to instruct an AI is not used as a source, is counted, and is named in the limits section. Only the first 12,000 characters of a page reach a model. Cancel: a watcher polls the task status every 1.5 s and stops searching, fetching and model calls at once (the cancel test finishes in well under 5 s).
6. **Budgets.** Settings `MAYA_DEEP_RESEARCH_*` (see `docs/guides/CONFIGURATION.md`): enabled, minimum domains, maximum sources, hard maximum, time cap, model-call cap, rounds, fetch concurrency. Reading stops at 70 percent of the time cap so writing has time; map calls stop 8 calls before the model-call cap so analysis and writing always fit. Kill switch `MAYA_DEEP_RESEARCH_ENABLED=false`, and the mode fails closed with a plain message when no search provider (other than Gemini grounding, whose links are redirects) or no model client exists.
7. **Progress.** Steps through the existing task progress outbox, nested under the research step: `Planned 8 questions`, `Searching (query 12 of 31)`, `Read 12 of 24 sources`, `Skipped 3 (3 paywall)`, `Analysing the sources`, `Writing`, `Checking citations`. The voice digest of a finished step says `Deep research: N sources read from M sites, K skipped`.

## Privacy

Queries and fetches leave the machine: search queries go to the configured providers (DuckDuckGo, optionally Brave or Tavily), and page requests go to the sites found, with the MAYA user agent. Page text and notes go to the Gemini models in the untrusted envelope. The user's request text appears in queries. Nothing else (files, memory, other chats) is sent. Pages are not stored; the result and its sources are the task output.

## Consequences

- A run costs up to about 60 model calls plus 40 page fetches and takes up to 10 minutes. Bot-blocking sites will lower the number read; that is reported, not hidden.
- Source scoring is a transparent heuristic. The decision service is used for the injection screen and the grounding check only; it is not asked to rank sources (no new decision purpose).
- Round-1 reading keeps a quarter of the page budget (at most 12 pages) for the gap round or a top-up read.

## Unverified claims

Tested only with fakes (no live search, fetch or Gemini call). Unknown until a real run: how many real sites block or paywall (and so the real reads per run), the cost per run, the quality of the notes and the written result, how well ddgs returns about 80 distinct candidates for real topics, and whether the Gemini lite model reliably returns the notes schema under load. The grounding check is advisory. Scanned PDFs have no text layer and are skipped.
