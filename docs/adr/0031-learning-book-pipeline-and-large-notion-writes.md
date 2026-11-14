# ADR-0031: Learning-book pipeline and large Notion writes

- Status: Proposed
- Date: 2026-11-13

## Context

MAYA's Notion pages came out short because the chat model wrote the whole page inside the arguments of one tool call, which is limited by output size (a few hundred words). PDFs already used the long-document pipeline (ADR-0020: one plan call, one Markdown call per chapter, three at a time, deterministic assembly). The owner decided: every notes request (PDF, DOCX and Notion) uses the owner's "Master Learning Book" prompt adapted to MAYA; a Notion notes page is a book of 8 to 12 chapters and about 6,000 to 10,000 words; the default code language is the user's saved preference (ask when there is none and it matters, never a hard-coded language); the approval card shows the outline, the counts and the exact content, and approving creates the page and reads it back.

## Decision

1. **Prompts** (`backend/app/agents/prompts/learning_book.py`): the owner's prompt is split into a plan prompt (8 to 12 chapters, goals, word budgets, `needs_code`, `needs_table`, a `story_premise` and one `story_beat` per chapter) and a chapter prompt with the section template of the chapter's role (opening, concept, coding problem, closing with interview questions and the final revision material). Chapters run in parallel, so the story thread travels in the plan: each chapter receives the premise and the beats of the chapters before it ("story so far"). Rules kept: story first, foundations to advanced, terms defined before use, simple short sentences, `#`, `##` and `###` only, LaTeX unescaped, labelled code fences, algorithm traces as numbered steps with child bullets, no invented facts or sources. The learner level defaults to BEGINNER. Notes are simple English unless the user asked for Hindi or Hinglish. The code language is named in the request, else the user's saved preference, else unnamed (language-neutral pseudocode, never Java by default); a programming book with no language asks once through `ask_user` before the job starts.
2. **Routing**: `is_learning_book_request` (study, master, learning or revision notes, "notes on X", a book on X; not meeting or release notes, "add a note", "my notes") in the planner and in `long_route` (pdf, docx) builds the pipeline in code (`learning_book_plan.py`, `pipeline["mode"] == "learning_book"`) and reuses `run_outline`, `run_chapter`, the chapter concurrency of 3, the retry and the single expansion round. A PDF or DOCX with a STATED length ("10 pages") keeps the long-document pipeline and its prompts, because that pipeline enforces the length on the file; a Notion page has no enforced length, so a stated length (up to about 11,500 words) only sizes the book. For Notion the chat's `notion_create_page` call is replaced by the job (the parent page and title are passed on the instruction).
3. **Sources**: attached text or files are the input; otherwise MAYA's grounded web search finds 2 to 4 companions and the opening chapter lists them under "Explored Sources" only when real links came back.
4. **Assembly**: for Notion the assembly is ONE Markdown file (a `[toc]` marker after the intro line, `# Chapter N: title`, `##`, `###`), written by the document agent as an `.md` artifact. PDF and DOCX keep the block assembly.
5. **Notion write** (`backend/app/mcp/notion_book.py`, tool `notion_create_page_from_job`, APPROVAL_REQUIRED, never exposed to the chat model): the worker prepares the approval just before asking and binds it to the artifact id, file name, SHA-256 of the Markdown, block count and counts (the payload hash covers them). The approved run re-reads the file, refuses on any hash or block-count difference, creates the page with the first batch (emoji icon, an https cover only from a short allow-list) and appends the rest in requests of at most 100 blocks, 900 elements and 400 KB. It waits and resends only after a 429 and never truncates (long text and tables over 100 rows are split by the converter). Then it reads the page back and compares block count, headings, types and text. A failure part-way reports the exact number written and the arguments to continue with (`resume_page_id`, `resume_from_block`, after a new approval). `notion_markdown` gains the `[toc]` marker as a `table_of_contents` block.
6. **Approval guard**: the new tool is in `ALWAYS_MANUAL_TOOLS` and a write of more than 300 blocks is never auto-approved, whatever the scopes. `permissions.py` got one additive entry for the new tool name.
7. **UI**: the approval card (chat inline approval and Task card) shows title, parent, counts, the chapter outline, the first 40 lines and the full text on demand, rendered with the chat Markdown renderer a few chapters at a time. The exact payload and the Approve and Reject buttons are unchanged.

## Consequences

- A notes request takes minutes (about 11 to 14 model calls) instead of one call, and costs accordingly.
- A book is one page of up to 5,000 top-level blocks; larger content must be split.
- The Markdown file is the source of truth: editing it after approval cancels the write.

## Unverified claims

No real Gemini or Notion call was made. The output quality and length of the prompts (6,000 to 10,000 words), the grounded-search source list, Notion's acceptance of the `table_of_contents` block, the icon and cover fields, its request limits and its 429 behaviour, and the read-back of a 600-block page are tested only against fakes. Continuing a half-written page is implemented and tested but not yet offered as a chat action.
