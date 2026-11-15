# Document generation

How MAYA AI turns a task into a themed PDF, PPTX or DOCX. Decision record: [ADR-0017](../adr/0017-themed-document-generation.md). Code: `backend/app/automation/`. Module reference: [Document automation](../modules/document_automation.md).

**Status:** Implemented locally and tested with a real Microsoft Edge on Windows. Not run against the real Gemini API. PPTX and DOCX were checked structurally; their appearance in Office was not reviewed.

## Pipeline

```text
Gemini (structured call)  ->  Document / Deck model  ->  fixed renderers  ->  verifier  ->  proof
   fills JSON only            strict, size-bounded       we escape output     deterministic    renderer + theme
```

1. **Spec.** `FleetSupport._document_context` (`app/tasks/dispatch.py`) asks the model for a `DocumentSpec` with the system prompt `DOCUMENT_SYSTEM_PROMPT`, which lists the allowed file types, themes, block types and slide layouts and says "NEVER output HTML". `blocks` (pdf, docx) and `slides` (pptx) use the schema below. The legacy `sections` form stays valid.
2. **Validation.** The spec is validated with the models in `model.py`. A violation raises `NonRetryableSubtaskError` with the failing field paths (for example `blocks.3.table: table row 2 has 1 cells but there are 3 headers`).
3. **Render.** `DocumentAgent` calls `create_pdf_from_document`, `create_document_from_model` or `create_deck`. PDF and DOCX run in a worker thread so the event loop stays free.
4. **Verify.** `verify_pdf`, `verify_docx`, `verify_pptx`. The task worker still requires `status == "verified"`.
5. **Proof.** The stored artifact report carries `checks_passed`, `errors`, `renderer`, `theme`, `warnings` and, for PDF, `browser` and `fonts`.

## The model (`model.py`)

| Document block | Fields                                                 | Notes                                    |
| :------------- | :----------------------------------------------------- | :--------------------------------------- |
| `heading`      | `level` 1-3, `text`                                    | DOCX keeps levels valid (see below)      |
| `paragraph`    | `text`                                                 | Inline markup allowed                    |
| `bullets`      | `items`                                                | max 50 items                             |
| `numbered`     | `items`                                                | max 50 items                             |
| `table`        | `headers`, `rows`, `caption?`                          | max 12 columns, 200 rows; rows rectangular |
| `callout`      | `kind` info, warning or success; `text`                |                                          |
| `metrics`      | `items` of `{value, label}`                            | max 6                                    |
| `quote`        | `text`, `by?`                                          |                                          |
| `image`        | `src`, `alt`, `caption?`                               | data URI or workspace-relative path only |
| `code`         | `language`, `text`                                     | max 8,000 characters                     |
| `page_break`   |                                                        | leading, trailing and repeated ones dropped |

`Document(theme, title, subtitle?, author?, date?, blocks)`: at most 200 blocks, 5,000 characters per paragraph, 400,000 characters in total.

`Deck(theme, title, slides)`: at most 60 slides. Slide layouts: `title`, `section`, `bullets`, `two_column`, `cards`, `metrics`, `quote`, `table`, `image`, `timeline`, `closing`; each slide may carry speaker `notes`. Per-layout requirements are checked (for example `timeline` needs 2-6 steps). Caps keep text inside the slide at the smallest allowed font (bullets 8 x 160 characters, cards 1-4 with 400-character bodies, titles 100 characters).

**Inline markup** (`inline.py`): `**bold**`, `*italic*`, `` `code` ``, `[label](url)`. Only `http`, `https` and `mailto` links survive; `javascript:`, `data:` and everything else degrade to plain text. Renderers turn spans into their own format and escape text themselves.

## Themes (`themes.py`)

`executive` (navy with a serif heading font and gold accent), `minimal` (light, blue accent), `midnight` (dark). A theme is plain data: colors, CSS font stacks, single family names for PPTX and DOCX, sizes and spacing. Every font stack ends with a Unicode-capable fallback (Nirmala UI, Noto Sans Devanagari, Mangal, Arial Unicode MS) and DOCX/PPTX runs set the complex-script font, so Devanagari and Hinglish text renders. An unknown theme name is a validation error, never a silent default.

## PDF (`html_render.py`, `browser_pdf.py`, `pdf_engine.py`)

- `render_document_html(doc)` is a pure function: one self-contained HTML string with inline CSS, `@page` size A4 with margins, running header (title, pages 2+), footer with `Page N of M`, `break-inside: avoid` for short tables, callouts, quotes, code and figures, and repeated table header rows. Long tables flow across pages. A deny-by-default Content-Security-Policy meta tag is included. `document_preview_html(doc_or_dict)` returns the same HTML for a later UI preview (use a sandboxed iframe `srcdoc`).
- `BrowserPdfRenderer` drives the installed Edge (`msedge`) or Chrome (`chrome`) through Playwright. JavaScript is disabled, service workers and downloads are blocked, and every request is aborted (the HTML is injected, never fetched). It owns a daemon thread with a private event loop (a proactor loop on Windows, because the application selects the selector loop for PostgreSQL), reuses one browser, and is closed by `shutdown_browser_renderer()` (called on application shutdown and at interpreter exit). Timeout 45 s.
- `create_pdf_from_document(doc, path, assets_root=None)` returns `PdfRenderResult(path, renderer, theme, warnings, browser)`. If no browser is found or rendering fails it falls back to ReportLab and records `renderer == "reportlab-fallback"` and a warning. `create_pdf(title, sections, output_path, subtitle=None, theme="executive")` keeps the legacy signature.

## PPTX (`pptx_engine.py`)

`create_deck(deck, path, assets_root=None)` returns `DeckRenderResult`. Shapes are native: background fill, rectangles, rounded cards, text boxes with real bullets, a native table, ovals and lines for the timeline, pictures. Font size is the largest whole point size that fits by a conservative character-count estimate (`textfit.py`); if nothing fits at the smallest size the render fails with `Slide N: ... does not fit`. Slide numbers are native fields, speaker notes are written, text runs carry the Latin and complex-script fonts, links are real hyperlinks. `create_presentation(slides_data, output_path)` keeps the legacy API and maps legacy dicts to the same renderer.

## DOCX (`docx_engine.py`)

`create_document_from_model(doc, path, assets_root=None)` returns `DocxRenderResult`. Real Word heading, list and title styles; shaded and bordered paragraphs for callouts, quotes and code; shaded tables with a repeating header row; header and footer with page fields; dark themes set the page background. Heading levels are clamped so a heading never skips its parent (a warning is recorded). `create_document(sections, output_path, title, subtitle, theme)` keeps the legacy API.

## Verification

| Format | Checks added (existing checks unchanged)                                                                                   |
| :----- | :------------------------------------------------------------------------------------------------------------------------- |
| PDF    | page count, text extractable, fonts used and whether any are neither embedded nor standard, size                          |
| PPTX   | slide count, editable text exists (a picture-only deck fails), no text box over the overflow estimate, notes and picture counts |
| DOCX   | unchanged (structure, heading order, table shape)                                                                          |

The overflow check is an estimate, not a font measurement. A pass does not mean the slide was looked at.

## Acceptance criteria and long documents

- **Acceptance** (`automation/acceptance.py`): `{pages?, slides?, words?, sheets?}`, only what the user stated ("50 pages", "8-slide", "50 page ka", "fifty pages", "2k words"; a range gives its midpoint). Checked after rendering: pages within -10%/+25% (at least +-1 page for short documents), slides and sheets exactly, words >= 85%. DOCX has no fixed pages, so a page requirement is not claimed for it. A miss, even under half of the request, is a warning on a delivered file ("You asked for X; the file has Y"), never a failure (see "Count policy" below).
- **Long documents** (> 5 pages or > 3500 words): a small plan call (JSON), then one MARKDOWN call per chapter (parsed by `app/automation/md_blocks.py`, no JSON for prose; one retry naming the problem, structured JSON call as last fallback, at most 3 chapter calls at once) -> assemble, see [Agent request flow](AGENT_REQUEST_FLOW.md#8-long-documents-and-length-acceptance). `Document.cover_page` and `Document.toc` switch on the cover page and contents list.
- **Limits** (`model.py`): `MAX_BLOCKS` 1500, `MAX_DOCUMENT_CHARS` 800 000 (about 100 pages); per-block caps unchanged. Document agent timeout: 60 s + 6 s per requested page, 240 s minimum, 10 min maximum. Browser print budget: 45 s up to 240 s, growing with the block count. Measured (Edge 157, notes theme, 63 pages, 443 blocks, two prints): 2.6 s cold, 1.3 s warm, a 340 KB file.
- **`notes` theme**: clean, white, print-friendly, teal accent, line height 1.55; also used for PPTX/DOCX like the others. Callouts are labelled Note (info), Tip (success), Warning (warning); code is labelled, monospace and wraps long lines; table header rows repeat across pages; every page has "Page X of Y"; long code blocks may continue on the next page.
- **Contents page numbers**: the HTML gives each heading an `id`; the browser writes named destinations, so the first print learns each heading's page exactly (`pdf_engine.heading_pages`), the second prints the numbers (the number column is reserved, so layout cannot move). If a page cannot be measured or the two prints disagree, the contents list has no numbers and a warning says so; no number is ever guessed. DOCX lists the headings without numbers. The ReportLab fallback prints no cover or contents (warning).
- **Not built**: a running header with the current chapter title (Chromium does not support `string-set`; the header shows the document title).

## Count policy: a count mismatch never rejects a file (2026-11-13)

Owner rule: if the user asks for X pages, slides or rows and the model produces more or fewer, the file is **never rejected or discarded**. The user-stated count is the only enforced count (the model's own `pages` guess stays a soft hint, `pages_planned`). Order of preference:

1. **Fit to the exact number**, in code, before any rejection is possible.
   - **Slides** (`automation/count_fit.py`, `fit_slides`): too many: adjacent plain bullet slides are merged (nothing lost), then the thinnest content slides are dropped and their text moves into the speaker notes of the slide before. The cover (first slide), an agenda and the closing (last slide) are always kept. Too few: the model gets its one existing expansion call (`heal_acceptance`; not asked when the deck has too MANY slides), then the fullest bullet slides are split in two, then clearly labelled `Appendix: Notes` slides are added just before the closing slide. The real file is measured after each rebuild (the pptx engine can split an overfull slide itself), up to 4 rebuilds.
   - **Pages** (PDF, DOCX; `page_fit.py`): the real page count (pypdf; DOCX through LibreOffice) drives the density fit (max 3 re-renders inside the readable limits). When that ends exactly one page off, one more render changes the content: one page short adds a labelled `Notes` page; one page over removes a trailing page break or a trailing Appendix / Sources / Notes section. The adjusted file is kept only if it lands on the count. A long (assembled) document that missed its band gets the same fit once, after its expand / trim / compact rounds (`fit_pages` in the healing trail).
   - **Rows** (xlsx, `sheet_gen.prepare_sheet`): a generated sheet is extended to the stated rows; the main (largest) sheet is cut to them when it has more (a Summary sheet is never cut).
2. **If it is still off, deliver anyway** with the honest line ("You asked for 10 pages; the file has 9 ..."). The acceptance verdict is a **warning** (`verification_proof["acceptance"]["status"] == "warning"`, plain `note`), the step is `completed` (never `failed`, never "Completed with errors" because of a count), and the chat result, the task result text and the spoken summary say the true numbers (`task_result.count_notes`). The old "severe" (under half) failure is gone: `severe` is information only.
3. **Ranges** (`extract_ranges`): "8-10 pages" accepts 8 to 10; "about / around / approximately / roughly N" accepts N +-15% (about 10 slides: 9 to 11; about 100 pages: 85 to 115). A plain "10 slides" is exact. A count inside its range is not fitted at all.

What still rejects: an empty, invalid or unverifiable file (empty deck or sheet, empty text, a verifier error such as picture-only slides, overflow outside the canvas). The pptx verifier's `expected_slides` mismatch is now a `warnings` entry, not an error. The chat tool (`build_document`) follows the same policy: `length_retry` no longer turns a count miss into an error that discards the file (it only returns thin PDF pages to the model once); `length_note` and `message` in the tool result carry the true count. The advisory step review (`decisions/step_review.py`) is told that a differing count is not a gap. Learning books, scheduled reports and deep-research files use the same document agent, so they follow the same policy. Tests: `tests/unit/test_count_policy.py`.

## Words floor, file names and item counts (Task FA)

- **Words floor** (`acceptance.py`): a page request must also reach `words_floor(pages) = ceil(pages x 450 x 0.65)` words (`WORDS_FLOOR_FRACTION`). Measured density: 15 pages and 3,400 words (227 a page; 250-340 on content pages) for a 12-page request, which the old page-only check accepted. A miss reads "Requested 12 pages, produced 15 pages but only 3,400 words (expected about 5,400)", is expanded once, and stays an honest warning (severe under half the floor).
- **File names**: the title-derived name keeps safe characters only, at most 80 characters (a longer title is cut and gets a 6-character hash so two long titles stay distinct), never a Windows reserved device name, no trailing dots or spaces. The document agent also keeps the whole path under 240 characters (`automation/paths.py`): a deep `MAYA_ARTIFACTS_DIR` shortens the name; a folder so deep that no usable name fits fails with a message naming `MAYA_ARTIFACTS_DIR`.
- **Items and sources**: "N tips/points/steps" is a requirement (`requested_item_count`); a deck with an exact slide count equal to N has one slide per item and no title slide (reported in the warnings), more slides keep the title slide; a spec with fewer items gets one repair call. Research citations are appended as a Sources section when the model wrote none.

## Operational notes

- **Browser required for best PDFs.** Edge or Chrome must be installed where the backend runs. Without it the proof says `reportlab-fallback` with a warning. Windows 11 ships Edge; a Linux container does not.
- **No download.** `playwright install` is never run; only the Python package is installed.
- **Troubleshooting.** The warning text carries the first line of the launch error (for example "Executable doesn't exist"). `app.automation.browser_pdf.browser_available()` reports whether a standard install was found.
- **Images.** A workspace-relative path is resolved under the artifact workspace folder only (no `..`, 5 MB limit); an unresolvable image becomes a visible placeholder and a warning.

## Form (notes, report, book) and the user's style

`app/automation/form.py` picks the form from the user's own words (`requirements.user_request`, English and Hinglish keywords; "notes" beats "book" beats "report"; the default is report). `render_options` turns it into render choices: notes have no cover page, no page break per chapter and a contents list only above 8 pages (a long document keeps a contents list); books keep cover, contents and a page per chapter. The same function applies what the user stated explicitly: theme (`notes`, `minimal`, `executive`, `midnight`), `density: compact`, and an accent colour (`Document.accent_color`, validated `#RRGGBB`, applied by `themes.themed` to accent, primary and link in the PDF and DOCX renderers). PPTX decks do not yet apply the accent colour (not implemented). The content rules for each form are added to the document, outline and chapter prompts together with the `USER REQUIREMENTS` block (`app/agents/requirements.py`).

## AI-designed documents (`design.py`)

Each request can get its own look. The model fills `DocumentSpec.design` (or `OutlineSpec.design` for long documents) in the call it already makes, so there is no extra round trip:

- `palette`: background, text, muted, accent, primary, link, surface (`#RRGGBB`).
- `heading_font` / `body_font` from `FONT_ALLOWLIST` (Calibri, Segoe UI, Arial, Verdana, Tahoma, Trebuchet MS, Century Gothic, Corbel, Candara, Georgia, Cambria, Times New Roman, Palatino Linotype, Garamond, Comic Sans MS, Impact: families that render in Edge print, PowerPoint and Word without embedding).
- `cover` (band, plain, bold, sidebar), `heading_style` (plain, rule, bar, caps), `table_style` (filled, lined, striped, minimal), `callout_style` (tinted, outlined, bar), `density` (compact, normal, airy), `body_pt` (9-13), `heading_scale` (0.85-1.35), `base` (a preset to start from), `mood`.

`resolve_design()` validates and repairs (never ignores): invalid hex or font or enum falls back to the preset's value; every colour that fails WCAG AA 4.5:1 against the background keeps its hue and is moved toward black or white until it passes; sizes are clamped. Each repair is returned as a note and stored in the spec healing trail. The result is a runtime `Theme` (with a `ThemeStyle`) used by `html_render` (all styles), `pdf_engine`, `docx_engine` and `pptx_engine` (palette and fonts; cover, heading, table and callout styles are browser-print only). The Document and Deck models carry the validated design as a dict.

Precedence: a theme the user stated replaces the design; a stated accent colour replaces the design's accent, primary and link (and is repaired for contrast on the background). No design means the named preset, unchanged.

## File type choice and projects (`filetype.py`, `zipper.py`)

The model picks the type from the request (`type_guidance()` is in the document prompt): pdf, docx, xlsx, pptx, md, txt, html, csv, json and code types (py, js, ts, jsx, tsx, css, sql, sh, ps1, java, c, cpp, cs, go, rs, rb, php, yaml, xml). Text types are written from `DocumentSpec.text` with the matching extension and validated (JSON and CSV parse, Python parses with `ast`, HTML is well formed; nothing is executed). `detect_file_type()` reads the user's words as a cross-check: a docx for a request that names another format gets one repair call.

A multi-file website (`needs_project()`: website, web app, vanilla html+css+js, separate files) is a `project` step handled by `project_writer.py`: with a connected folder every file is written separately into `<folder>/<project>/`; without one the files are packed into one `.zip` (`zipper.build_project_zip`: names validated against traversal, absolute and drive paths, reserved names and extension allowlist; at most 64 files, 5 MB per file, 20 MB total; sorted entries with fixed timestamps so output is deterministic; `verify_zip` re-checks it) and registered as `application/zip`. A single web page is one `.html`. The planner (not this module) chooses the `project` role.

## Research failure is reported, not hidden (`research_policy`)

When the research step gave no sources (`sources_unavailable`, or a failed dependency and no citations), `tasks/dispatch.research_policy()` decides in one place: `fail` when the request needs current or citable web facts (`needs_web_facts()`) and the user supplied no material: the document step fails before any model call with a plain reason; `general` otherwise: the file is built from the user's own content and general knowledge, the invented-figure scan and repair still run, and a visible "Not source-verified" line is added to the file and `not_source_verified` plus a plain note to the task result. Documents with sources are unchanged.



## Live-run fixes (2026-11-11)

- **Length planning for flowing documents:** `plan_chapters(acceptance, form)`: notes and reports have no cover and no chapter tails, so the chapters get the page budget minus a half-page title block, planned at least 10% over `words_floor(pages)` and no higher than the accepted page band. The expand round aims above the floor. Acceptance that still fails after the heal rounds is stated at the top of the result ("does NOT meet the length you asked for"), not only as a warning.
- **Contents without a cover:** a document without a cover page prints its contents list in the flow of page 1 (no page break after it).
- **Language and request echoes:** `requirements.detect_language` (explicit mention, Devanagari, Hinglish marker words, else English) feeds a fixed language line in every outline, chapter and expand prompt; a topic that is only the request's own framing ("Java notes") never becomes a subtopic, a bullet or a contents entry.
- **Colour roles:** `requirements.extract_palette_roles` turns colour words into background / text / heading / accent roles (dark page for "dark blue theme", coloured headings otherwise); `_apply_user_style` renders them as a design on the stated theme. Proofs report `theme: "custom"`, `base_theme` and the palette.
- **Deck and document guards (`content_guard.py`):** invented e-mail addresses and URLs are stripped; a title-only content slide gets one repair round.
- **Artifacts:** every produced type is stored with a `file_artifacts.file_type` the CHECK constraint accepts (`db_file_type`); see `app/tasks/worker.py::_register_artifacts`.

## Chat: long documents, table repair and the fill check (2026-11-12)

- **Route.** In the chat tool loop, `create_document` for a new pdf or docx whose page count is above `LONG_DOCUMENT_PAGES` (5) does not build inline: `long_route` hands the request to the existing chapter pipeline as a background job (`start_job` path, steps on the Tasks page) and the tool result says "building in the background, about N chapters". Edits of an earlier file and 5 pages or fewer stay a single call, written as one `spec.markdown` string (optional leading json plan fence; `append: true` with `edits_file_id` continues the earlier document).
- **Table rows** are reshaped before validation (`normalize.coerce_rows`): pipe strings, a markdown table, objects keyed by header, flat cell lists. A block that cannot be repaired is kept as a paragraph and named in the warnings. Errors name the block and its type and the shape wanted.
- **Fill check** (`page_fit.fill_report`): interior pages under 55% of the theme's words per page (cover, contents, last page and one short page per forced page break exempt) go back to the model once for real depth; otherwise a warning. A file under the asked page count is reported as "I produced N of M pages" with an offer to continue (`delivery: partial`).

## Learning books: notes for PDF, DOCX and Notion (ADR-0031)

A notes request (study, master, learning or revision notes, "notes on X", a book on X) is written as a connected, story-based learning book instead of one short model call.

- **Plan** (`app/agents/learning_book_plan.py`, in code, no model call): `outline` -> `chapter_1..N` (8 to 12, three at a time) -> `assemble`, plus `notion_page` for Notion. `pipeline["mode"] == "learning_book"` switches the prompts in `app/agents/prompts/learning_book.py`; the retry, fallback, concurrency and expansion round of ADR-0020 are reused. A PDF or DOCX with a stated length ("10 pages") keeps the long-document pipeline and its prompts (it enforces the length on the file); for Notion a stated length only sizes the book.
- **Prompts**: the plan call returns goals, word budgets, `needs_code`, `needs_table`, a story premise and a story beat per chapter. Each chapter prompt carries the premise and the beats of the earlier chapters ("story so far") and the section template of its role: opening, concept (Understanding, Why it matters, How it works, Components, Real-world example, Terminology, Common misconceptions, Chapter takeaway), coding problem (story, restate, input/output/constraints/edge cases, tiny example, brute force, better, optimized, comparison table, interview explanation) and closing (applications, interview or exam questions with answers, final revision material). Headings stop at `###`; LaTeX is unescaped; fences carry the real language; no invented facts or sources.
- **Code language**: named in the request, else the saved standing preference, else none (language-neutral pseudocode; never Java by default). A programming book with no language asks once with `ask_user` before the job starts. Notes are simple English unless Hindi or Hinglish was asked for.
- **Sources**: attached text or files are summarised as input; otherwise the grounded web search finds 2 to 4 sources, listed as "Explored Sources" only when real links came back.
- **Assembly**: PDF and DOCX use the block assembly (and the stated-length repair rounds). Notion gets one Markdown file (`app/automation/book_markdown.py`: `[toc]`, `# Chapter N: title`, `##`, `###`) stored as an `.md` artifact; each chapter's Markdown travels in its step output under `draft_markdown` (not in events).
- **Progress**: the Tasks page shows the plan step, `Chapter k: title` per chapter, assemble and, for Notion, the approval step; the voice digest says "Chapters: 4 of 10 written."
- **Notion delivery**: see `backend/app/mcp/README.md` (`notion_create_page_from_job`).
