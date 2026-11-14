# Document Generation & Automation Module (`app.automation`)

The `app.automation` module provides Phase 10 document generation engines and deterministic verifiers for MAYA AI.

## Architecture

The AI fills a structured model; fixed renderers (never model-written markup) turn it into files, and deterministic verifiers check the result. See [Document generation](../../../docs/architecture/DOCUMENT_GENERATION.md) and [ADR-0017](../../../docs/adr/0017-themed-document-generation.md).

| Module                    | Role                                                                                                                      |
| :------------------------ | :------------------------------------------------------------------------------------------------------------------------ |
| `model.py`                | Strict, size-bounded `Document` (blocks) and `Deck` (slides) models; legacy `sections`/slide mapping; error formatting    |
| `inline.py`               | Safe inline markup (`**bold**`, `*italic*`, `` `code` ``, links); only `http`, `https`, `mailto` links                     |
| `themes.py`               | `executive`, `minimal`, `midnight` as plain data (colors, font stacks with Unicode fallback, sizes)                       |
| `html_render.py`          | Pure `render_document_html` / `document_preview_html`: one escaped, self-contained HTML page with print CSS               |
| `browser_pdf.py`          | `BrowserPdfRenderer`: installed Edge/Chrome via Playwright, JavaScript off, all requests aborted, one reused browser      |
| `pdf_engine.py`           | `create_pdf_from_document` (browser, ReportLab fallback with warning) and legacy `create_pdf`                             |
| `pptx_engine.py`          | `create_deck` (native shapes, themes, text fit, notes) and legacy `create_presentation`                                   |
| `deck_themes.py`          | The slide theme library: 25 data-driven themes (12 `glass` dark-first, 13 `editorial` light-first), registered in `themes.py` |
| `deck_scene.py`, `deck_graphics.py`, `deck_layouts.py` | Renderer-neutral scene primitives; cards, motifs, header and Pillow background art; the library layouts (`hero_cover`, `section_break`, `stat_grid`, `icon_cards`, `process`, `image_text`, `chart`) |
| `deck_pptx_scene.py`, `deck_icons.py`, `deck_transitions.py` | Native shapes (alpha, gradients, shadows, freeform icons, charts), 47 Lucide-style icons (see `backend/THIRD_PARTY_NOTICES.md`) and `p:transition` (none, fade, push, wipe, cover) |
| `docx_engine.py`          | `create_document_from_model` and legacy `create_document`                                                                 |
| `textfit.py`, `pptx_fit.py` | Conservative text-fit estimates shared by the PPTX engine and verifier                                                  |
| `assets.py`               | Workspace-relative image loading (stays inside the workspace, 5 MB cap)                                                   |
| `xlsx_engine.py`          | Multi-tab workbook: typed cells and number formats, styled header, banding, widths, freeze panes, filter, key-column conditional formatting, a chart for small label + number tables                                           |
| `auto_design.py`          | Content-aware design defaults (tone presets, varied deck layouts, cover/contents choice); fills only what the model left out |
| `design_keep.py`          | Keeps the existing design when a file's content is edited; merges only the fields of an explicit restyle request          |
| `verifiers/`              | Deterministic quality, structure and security verifiers (below)                                                           |

Verifiers:

- `xlsx_verifier.py`: detects formula errors (`#REF!`, `#DIV/0!`), enforces `.xlsx` (prohibiting macro-enabled `.xlsm`), checks parenthesis balance.
- `docx_verifier.py`: XXE-safe XML parsing (`resolve_entities=False`), heading order validation, table column count validation.
- `pptx_verifier.py`: slide count, 16:9 aspect ratio, shape bounds, editable text present, estimated text overflow, notes and picture counts.
- `pdf_verifier.py`: page count thresholds, non-empty text layer via `pypdf`, fonts used and whether they are embedded.
- `schemas.py`: `VerificationReport` and SHA-256 computation.

## Usage

```python
from app.automation.model import Document
from app.automation.pdf_engine import create_pdf_from_document
from app.automation.verifiers.pdf_verifier import verify_pdf

doc = Document.model_validate(
    {
        "theme": "executive",
        "title": "Quarterly report",
        "blocks": [
            {"type": "heading", "level": 1, "text": "Summary"},
            {"type": "paragraph", "text": "Revenue grew **18%**."},
        ],
    }
)
result = create_pdf_from_document(
    doc, "report.pdf"
)  # blocking; use asyncio.to_thread in async code
print(result.renderer, result.warnings)  # "browser" or "reportlab-fallback"
assert verify_pdf(result.path).status == "verified"
```

PPTX: `create_deck(Deck(...), path)`. DOCX: `create_document_from_model(doc, path)`. The legacy `create_pdf`, `create_presentation` and `create_document` keep their signatures and now use the same themed renderers.

## Requirements and limits

- PDF output at full quality needs Microsoft Edge or Google Chrome on the machine (Playwright drives the installed browser; nothing is downloaded, never run `playwright install`). Without one, ReportLab is used and the proof says so. ReportLab cannot draw Devanagari.
- Browser, fonts and Office rendering differ by machine; verifiers check structure and estimates, not appearance.
- `shutdown_browser_renderer()` closes the shared browser (called from the application lifespan and at exit).

## Integration

Engines and verifiers are orchestrated by `app.services.artifact_service.ArtifactService` and served via `/api/v1/artifacts`.
For complete architecture and specification details, see [Document Automation Documentation](../../../docs/modules/document_automation.md) and [Artifacts Specification](../../../docs/api/ARTIFACTS_SPECIFICATION.md).

## Long documents (ADR-0020)

`acceptance.py` (stated length and its check), `long_document.py` (assembly, page estimate, trim), the `notes` theme, `Document.cover_page`/`toc` and the two-pass contents numbers in `pdf_engine.py`. See `docs/architecture/DOCUMENT_GENERATION.md`.

## Design defaults and edits

- A spec without `design` still gets one: `auto_design.pick_tone` reads the title, request and first
  text (English and Hinglish words) and `default_design` fills only the fields the model left empty
  (a palette the model chose is never touched; `resolve_design` still repairs contrast). Plain bullet
  decks are re-laid out from each slide's own content (`vary_layouts`; slides with another layout are
  left alone and no slide is added or lost). pdf/docx get a cover page and contents from the length
  (`document_flags`), never when a page count is stated.
- A content edit keeps the design: the chat tool loop stores the effective spec and its design facts
  with the file (`design_state` in the working state); `build_document(previous=...)` applies them
  (`design_keep.carry_design`). Only an explicit restyle request (`requests_restyle`) changes the
  fields it names. A PDF is rebuilt from the stored spec; docx, pptx and xlsx edited in place by the
  task path (`file_edit.py`) keep every style (appended docx tables copy an existing table).
