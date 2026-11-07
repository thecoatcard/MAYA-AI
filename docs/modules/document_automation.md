# Module: Document Automation & Deterministic Verifiers

- **Status:** Implemented
- **Engines:**
  - `backend/app/automation/xlsx_engine.py` (Excel generation via `openpyxl`)
  - `backend/app/automation/docx_engine.py` (Word document generation via `python-docx`)
  - `backend/app/automation/pptx_engine.py` (PowerPoint presentation generation via `python-pptx`)
  - `backend/app/automation/pdf_engine.py` (PDF document generation via `reportlab`)
- **Deterministic Verifiers:**
  - `backend/app/automation/verifiers/xlsx_verifier.py`
  - `backend/app/automation/verifiers/docx_verifier.py`
  - `backend/app/automation/verifiers/pptx_verifier.py`
  - `backend/app/automation/verifiers/pdf_verifier.py`
- **Storage & Delivery:**
  - `backend/app/services/artifact_service.py` (`ArtifactService`)
  - `backend/app/api/v1/artifacts.py` (`GET /api/v1/artifacts/{id}`)

---

## 1. Purpose & Design Principles

In accordance with [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) §13 and Principle 2 ("No success without proof"), generative models frequently emit corrupted files, broken formulas (`#REF!`, `#VALUE!`), overlapping text frames in presentations, or unextractable PDF documents.

MAYA AI enforces **pre-completion deterministic verification**:

1. All file artifacts default to `verification_status="pending"`.
2. Artifacts cannot be marked `completed` or presented to users until programmatic verifiers validate their integrity.
3. Every deliverable generates a structured `VerificationReport` with cryptographic SHA-256 hash, byte size, passed checks, and exact error locations.

---

## 2. Pipeline Specifications

### 2.1 Excel Pipeline (XLSX)

- **Engine:** `create_spreadsheet(sheets, output_path)` creates multi-tab `.xlsx` workbooks with styled header rows, frozen panes, thin borders, column number formats (currency, percentage), and auto-calculated column widths.
- **Verifier (`verify_xlsx`):**
  - Asserts file size is within 50 MB budget.
  - Enforces `.xlsx` extension (macro-enabled `.xlsm` strictly banned).
  - Inspects every cell across all worksheets for formula error tokens: `#REF!`, `#VALUE!`, `#DIV/0!`, `#NAME?`, `#N/A`, `#NULL!`, `#NUM!`.
  - Validates balanced parentheses and syntax prefixes on all formula cells.
  - Flags exact coordinates (e.g. `Sheet1!B5`) upon failure.

### 2.2 Word Document Pipeline (DOCX)

- **Engine:** `create_document(sections, output_path, title, subtitle)` produces styled `.docx` reports with 1-inch margins, title blocks, styled headings (H1, H2, H3), paragraphs, callouts, and center-aligned grid tables.
- **Verifier (`verify_docx`):**
  - Parses `word/document.xml` using `lxml.etree` with `resolve_entities=False` to defend against XXE attacks and XML entity bombs.
  - Enforces heading hierarchy consistency (Heading 2 cannot precede Heading 1; Heading 3 cannot precede Heading 2).
  - Validates table structure (matching column counts across all rows and non-empty header rows).

### 2.3 PowerPoint Pipeline (PPTX)

- **Engine:** `create_presentation(slides_data, output_path)` generates 16:9 widescreen presentations with title slides, multi-column card layouts, metric highlights, and bulleted lists.
- **Verifier (`verify_pptx`):**
  - Asserts slide count matches expected count (e.g. 8-slide deck for Workflow 7).
  - Confirms 16:9 widescreen canvas aspect ratio (`13.333 x 7.5` inches).
  - Performs **bounding-box boundary checking**: computes `(left + width, top + height)` for every shape on every slide, asserting that all shapes remain strictly inside the canvas boundary (preventing visual text overflow).

### 2.4 PDF Pipeline (PDF)

- **Engine:** `create_pdf(title, sections, output_path, subtitle)` compiles structured multi-page PDF documents with running headers and two-pass `Page X of Y` footers using `NumberedCanvas`.
- **Verifier (`verify_pdf`):**
  - Verifies non-zero page count and minimum page thresholds.
  - Inspects the text layer across all pages using `pypdf`, asserting that text is extractable and non-empty (detecting blank canvases or corrupt font mappings).

---

## 3. Storage & Tenant Isolation

Artifacts are stored on disk in workspace-partitioned directories:
`storage/artifacts/{workspace_id}/{task_id}/{file_name}`

Database records in `file_artifacts` strictly enforce PostgreSQL Row-Level Security:

- Cross-tenant queries are blocked by RLS policies.
- Direct downloads via `GET /api/v1/artifacts/{id}` require valid tenant JWT and match `workspace_id`.
