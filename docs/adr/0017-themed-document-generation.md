# ADR-0017: Themed document generation (browser PDF, native PPTX, DOCX)

- **Status:** Proposed (the owner approved the decisions below on 2026-11-09; this ADR becomes Accepted when the owner signs it off with the first deployment that ships the browser requirement)
- **Date:** 2026-11-09
- **Deciders:** Repository owner
- **Related:** [Document generation](../architecture/DOCUMENT_GENERATION.md), [Document automation module](../modules/document_automation.md), [ADR-0011](0011-mcp-gateway-and-specialist-agents.md), [ADR-0013](0013-desktop-shell-electron.md)

## Context

The document agent produced plain PDFs (ReportLab), a fixed blue deck and a simple DOCX. The look was inconsistent across formats, ReportLab's built-in fonts cannot draw Devanagari (Hinglish content), and the model could be asked for free-form content with no schema or size limits. The owner wanted professional, themed output, native editable PowerPoint, and safe handling of model-written content.

## Decision

1. **The model fills a structured document model; we render it.** `app/automation/model.py` defines `Document` (themed blocks) and `Deck` (themed slides) with strict, size-bounded pydantic validation. The model never writes HTML or any markup. Text supports only a tiny inline syntax (`**bold**`, `*italic*`, `` `code` ``, `[label](url)`) parsed by our own function, with `http`, `https` and `mailto` as the only link schemes. Images are a base64 `data:` URI or a workspace-relative path; URLs are rejected.
2. **One theme object drives every format.** `app/automation/themes.py` holds plain data for `executive`, `minimal` and `midnight` (colors, font stacks with a Unicode fallback, sizes, spacing). PDF, PPTX and DOCX all read it.
3. **PDF: HTML template, headless system browser, Playwright (Python).** One fixed, escaped HTML template (`html_render.py`, a pure function) is printed by the installed Microsoft Edge or Google Chrome (`channel="msedge"` or `"chrome"`; no browser download, `playwright install` is never run). The browser context has JavaScript disabled and every network request aborted, and the page carries a deny-by-default Content-Security-Policy. One browser is reused per process on a private event loop and closed at application shutdown.
4. **Fallback is explicit.** If no browser is found or it fails, the ReportLab renderer produces the file and the verification proof records `renderer: "reportlab-fallback"` plus a warning. Non-Latin scripts are warned about because ReportLab's fonts cannot draw them. The proof never claims the browser was used when it was not.
5. **PPTX: native editable shapes and theme colors via python-pptx.** Text boxes, rectangles, native tables and pictures only; no slides rendered as images. Font sizes follow a conservative character-count fit; text that cannot fit fails with a precise message. Speaker notes and a native slide-number field are written.
6. **DOCX** renders the same `Document` with Word styles, shaded callouts and repeating table header rows.
7. **Verification is extended, not weakened.** PDF proofs list page count, text extractability, fonts and embedding; PPTX proofs count slides, notes and editable text and flag text boxes over a conservative overflow estimate; the proof records `renderer` and `theme`. The `status == "verified"` contract used by the task worker is unchanged.

## Consequences

- **New dependency (approved):** `playwright==1.64.1` (with its pure-Python requirement `pyee`) in `backend/pyproject.toml` and `backend/uv.lock`. No other dependency was added (no template engine, markdown library or other PDF library).
- **Browser requirement:** best PDF output needs Microsoft Edge or Google Chrome on the machine that runs the backend. Windows 11 includes Edge. A Linux container image has no browser, so there the ReportLab fallback is used (visible in the proof) unless a browser is installed in the image.
- **Desktop installer note:** the Electron desktop shell ([ADR-0013](0013-desktop-shell-electron.md)) must document that Edge or Chrome has to be present, and the installer should check for it. It does not bundle a browser.
- **Fallback quality gap:** fallback PDFs are plainer and cannot render Devanagari; users see a warning in the artifact report.
- **Process cost:** the first PDF starts a browser (about 1.5 s); later documents reuse it (about 0.5 s). The browser runs outside the Python process; shutdown closes it.
- **Stricter inputs:** model output that violates a cap (blocks, rows, text length, slide layout fields) now fails with a precise, non-retryable message instead of producing a file. Legacy `sections` and legacy slide fields remain accepted.
- **Not verified here:** rendering of PPTX and DOCX in real Office (no Office automation was run); the proof reflects structural and estimated checks, not a visual review.

## Alternatives considered

- **ReportLab only:** rejected; no Devanagari, weak typography, a second styling system to maintain.
- **WeasyPrint or other HTML-to-PDF libraries:** rejected (new native dependencies; the owner approved only Playwright).
- **Letting the model write HTML or Markdown:** rejected (script and exfiltration risk, unbounded size, inconsistent design).
- **Slides rendered as images:** rejected; not editable.
- **Bundling Chromium (`playwright install`):** rejected; large download, and Edge or Chrome is already present on the target desktops.
