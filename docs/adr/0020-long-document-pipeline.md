# ADR-0020: Long-document pipeline and honest length acceptance

- **Status:** Proposed (owner request, 2026-11-09)
- **Date:** 2026-11-09
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0017](0017-themed-document-generation.md), [ADR-0019](0019-self-healing-task-execution.md), [Agent request flow](../architecture/AGENT_REQUEST_FLOW.md#8-long-documents-and-length-acceptance), [Document generation](../architecture/DOCUMENT_GENERATION.md#acceptance-criteria-and-long-documents)

## Context

The owner asked: "make a notes on java from basic to advance make sure pdf is of 50 pages and well formatted". MAYA made a 2-step task, it showed "Completed", and the PDF had 3 pages. Two root causes: the whole document was ONE structured model call (a few pages at most, nothing decomposed a long deliverable), and the requested length was never a requirement (`verify_pdf(min_pages=1)` was never overridden), so a 3-page file was "verified". `MAX_BLOCKS=200` and a 240 s budget could not hold 50 pages either.

## Decision

1. **Acceptance criteria.** Quantities the user states (`pages`, `slides`, `words`, `sheets`) are extracted in code (`automation/acceptance.py`; English, Hinglish, number words, ranges) and carried in the existing JSON `subtasks.input_data["acceptance"]` (no migration). The document step verifies against them: pages -10%/+25%, slides and sheets exactly, words >= 85%. One bounded heal round, then an honest result: "Requested 50 pages, produced 38". A result under half of the request fails the step; a milder miss completes with the sentence in the task `warnings` (the existing convention; a `completed_with_warnings` status would need a migration).
2. **Chapter pipeline.** For more than 8 pages (or 3500 words) the plan is built in code, without a model call: `outline` -> N `chapter_i` (parallel, bounded by the worker pool, max 16, non-critical) -> `assemble` (document agent, deterministic, no model call). A chapter that fails after the existing retries is reported missing; the rest is assembled.
3. **Expand/trim.** After the real print, under 90% of the target: the thinnest chapters are rewritten once with more depth (parallel, bounded). Over 125%: whole low-priority sections are dropped (Exercises, takeaways, quiz sections, then last sections), never mid-section. The trail is in the proof `healing`.
4. **Limits.** `MAX_BLOCKS` 200 -> 1500, `MAX_DOCUMENT_CHARS` 400k -> 800k (about 100 pages); document timeout 60 s + 6 s/page (240 s to 10 min); browser print budget grows with blocks.
5. **Layout.** A `notes` theme, a cover page, a contents list whose page numbers are read from the PDF's named destinations in a second print (never guessed), page breaks per chapter, Note/Tip/Warning callouts.

## Consequences

- A long document takes minutes and many model calls (one outline, N chapters, possibly up to 6 rewrites). Cost and latency scale with pages.
- Page yield per chapter is unverified against real Gemini; the words-per-page assumption is 450 nominal (measured prose density 555), with the expand/trim round as the safety net.
- Chapter drafts live in `subtasks.output_result.draft` (about 20 KB each) and are returned by `GET /api/v1/tasks/{id}`; they are kept out of events and the stored plan. Moving drafts out of the API response is a follow-up (API owner).
- Requests above 100 pages are planned as 100 and reported honestly.

## Alternatives

Raise output limits of the single call (still one call, no verification); a dynamic DAG expanded at run time from the outline (needs a worker change to insert rows; chapter slots are planned up front instead and the outline is fitted to them); a `completed_with_warnings` status (needs a migration).
