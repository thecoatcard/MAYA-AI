# Phase 10 — File Processing and Document Generation

## A. Phase Overview

- **Phase Number:** Phase 10
- **Phase Name:** File Processing and Document Generation
- **Objective:** Build the document generation and deterministic verification engines for XLSX, DOCX, PPTX, and PDF formats, establish secure artifact storage in `/artifacts`, enforce pre-completion structural and mathematical validation, and implement the artifact download API.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §13 and Principle 2 ("No success without proof"), generative models frequently emit corrupted files, broken formulas (`#REF!`, `#VALUE!`), overlapping text frames in presentations, or empty PDF documents. A file must default to status `pending` (resolving Defect #4) and cannot be marked `completed` or presented to users until programmatic verifiers validate its integrity.
- **Expected Outcome:** Four generation and verification pipelines:
  - Excel (`openpyxl`, `pandas`): formula syntax and evaluation verification.
  - Word (`python-docx`, `lxml`): XML structure and heading hierarchy validation.
  - PowerPoint (`python-pptx`): slide count and text bounding-box overflow checks.
  - PDF (`weasyprint`, `pdfplumber`, `pypdf`): font embedding and text layer extraction.
  - S3-compatible artifact storage service and download endpoint `GET /api/v1/artifacts/{id}`.
- **Priority:** High
- **Estimated Complexity:** Medium
- **Estimated Duration:** 10 business days (Weeks 15–17 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security) and Phase 09 (Specialist AI Agents).
- **Required Infrastructure:** Running PostgreSQL 17 with `file_artifacts` table; MinIO / S3 object storage; Python rendering libraries (`weasyprint`, `openpyxl`, `python-docx`, `python-pptx`, `pdfplumber`, `lxml`).
- **Required Credentials or External Services:** Local MinIO credentials or AWS S3 credentials.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §13 (Document pipeline and deterministic verification) and §2.3 (Defect #4: `verification_status` defaults to `pending`).
- **Required Documentation:** `docs/architecture/DATA_MODEL.md`.

---

## C. Scope

### Included Features

- Spreadsheet Engine (`xlsx_engine.py`):
  - Generate multi-tab spreadsheets with formulas, formatting, and frozen headers via `openpyxl`.
  - Deterministic XLSX Verifier: re-open workbook, check for formula errors (`#REF!`, `#VALUE!`, `#NAME?`), confirm row/column counts, evaluate formula outputs with calculation harness.
- Document Engine (`docx_engine.py`):
  - Generate styled Word documents with heading hierarchies, callouts, and tables.
  - Deterministic DOCX Verifier: parse raw `document.xml` via `lxml`, confirm schema validity, verify non-empty body and consistent style hierarchy.
- Presentation Engine (`pptx_engine.py`):
  - Generate 16:9 PowerPoint decks from outlines using `python-pptx`.
  - Deterministic PPTX Verifier: verify slide count matches request, compute bounding-box boundaries to prevent text overflow outside canvas.
- PDF Engine (`pdf_engine.py`):
  - Compile HTML/CSS templates to PDF using `weasyprint`.
  - Deterministic PDF Verifier: verify non-zero page count, extractable text layer via `pdfplumber`, embedded fonts, render-to-image smoke test.
- Artifact Storage Service:
  - Stream generated files to S3/MinIO bucket.
  - Record SHA-256 hash, byte size, and full verification report in `file_artifacts`.
- Artifact Delivery API:
  - `GET /api/v1/artifacts/{id}`: Secure download with content-disposition and RLS verification.
- Enables Acceptance Workflows:
  - Workflow 5: Deep competitive research -> verified PDF.
  - Workflow 6: PDF invoices -> formatted, formula-verified Excel.
  - Workflow 7: Brief -> 8-slide PowerPoint deck without text overflow.

### Excluded Features

- In-browser interactive spreadsheet formula editor (deferred to Phase 14).
- OCR processing for handwritten scanned documents (post-MVP).

### Deferred Features

- Adobe InDesign / Illustrator template automation.

### Dependencies on Future Phases

- Phase 14 (Frontend Experience) embeds the deliverable canvas previewing files generated here.
- Phase 15 runs automated verification suites on Workflows 5, 6, and 7.

---

## D. Implementation Breakdown

### Task P10-T001: Excel Generation and Deterministic Verifier (Workflow 6)

- **Task ID:** P10-T001
- **Task Name:** Build XLSX Engine with Mathematical and Formula Verifier
- **Detailed Description:** Implement backend/app/automation/xlsx_engine.py (planned artifact). Ingest tabular invoice or financial data; format cells with accounting formats, borders, and auto-fitted columns. Implement `verify_xlsx(file_path: Path) -> VerificationReport`:
  - Open workbook with `openpyxl.load_workbook(data_only=False)`.
  - Scan all cells across all sheets; assert zero cells evaluate to `#REF!`, `#VALUE!`, `#DIV/0!`, or `#NAME?`.
  - Verify that summary formula ranges cover all inserted data rows.
  - Return `VerificationReport(status="verified", details={...})` or `status="failed"`.
- **Expected Files:** backend/app/automation/xlsx_engine.py (planned artifact), backend/app/automation/verifiers/xlsx_verifier.py (planned artifact), backend/tests/unit/test_xlsx_verifier.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Data Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_xlsx_verifier.py` (testing clean workbooks, formula errors, truncated sheets).
- **Documentation Updates:** docs/modules/document_automation.md (planned artifact).
- **Acceptance Criteria:** Valid workbooks pass verification; workbooks containing broken formulas or truncated rows are flagged `failed` with exact cell coordinates.

### Task P10-T002: Word Document Generation and Structural XML Verifier

- **Task ID:** P10-T002
- **Task Name:** Build DOCX Engine with XML Schema Verifier
- **Detailed Description:** Implement backend/app/automation/docx_engine.py (planned artifact) using `python-docx`. Generate structured reports with styled title blocks, nested numbered headings, tables with header styling, and bulleted lists. Implement `verify_docx(file_path: Path) -> VerificationReport`:
  - Unpack `.docx` package and parse `word/document.xml` using `lxml.etree`.
  - Assert XML well-formedness and schema conformity.
  - Verify paragraph style hierarchy (e.g. Heading 2 cannot appear before Heading 1).
  - Verify all tables contain non-empty headers and matching column counts.
- **Expected Files:** backend/app/automation/docx_engine.py (planned artifact), backend/app/automation/verifiers/docx_verifier.py (planned artifact), backend/tests/unit/test_docx_verifier.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Document Automation Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_docx_verifier.py`.
- **Documentation Updates:** docs/modules/document_automation.md (planned artifact).
- **Acceptance Criteria:** Word documents generate cleanly; corrupt XML or malformed heading structures are caught and marked `failed`.

### Task P10-T003: PowerPoint Deck Generation and Overflow Verifier (Workflow 7)

- **Task ID:** P10-T003
- **Task Name:** Build PPTX Engine with Canvas Bounding-Box Verifier
- **Detailed Description:** Implement backend/app/automation/pptx_engine.py (planned artifact) using `python-pptx`. Support 16:9 widescreen layout, slide master layouts, title slides, bulleted cards, and metric callouts. Implement `verify_pptx(file_path: Path, expected_slides: int = 8) -> VerificationReport`:
  - Open presentation and assert `len(prs.slides) == expected_slides`.
  - For each slide and each shape, compute shape boundaries `(left + width, top + height)`.
  - Assert that all shape boundaries remain strictly within the 16:9 canvas dimensions (13.33 x 7.5 inches) to prevent visual overflow.
- **Expected Files:** backend/app/automation/pptx_engine.py (planned artifact), backend/app/automation/verifiers/pptx_verifier.py (planned artifact), backend/tests/unit/test_pptx_verifier.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Presentation Automation Specialist.
- **Required Tests:** `pytest backend/tests/unit/test_pptx_verifier.py` (slide count check, overflow detection).
- **Documentation Updates:** docs/modules/document_automation.md (planned artifact).
- **Acceptance Criteria:** Generates 8-slide presentation matching brief; text overflow outside canvas triggers verification failure; aspect ratio is verified 16:9.

### Task P10-T004: PDF Generation and Rendering Verifier (Workflow 5)

- **Task ID:** P10-T004
- **Task Name:** Build PDF Engine with Multi-Layer Text Verifier
- **Detailed Description:** Implement backend/app/automation/pdf_engine.py (planned artifact) compiling HTML/CSS templates to PDF using `weasyprint`. Implement `verify_pdf(file_path: Path) -> VerificationReport`:
  - Verify process exit code is 0.
  - Parse with `pypdf` ensuring page count > 0.
  - Inspect with `pdfplumber` ensuring text layer is non-empty and extractable (not a blank canvas or corrupted font map).
  - Verify all fonts are embedded without rendering substitution errors.
- **Expected Files:** backend/app/automation/pdf_engine.py (planned artifact), backend/app/automation/verifiers/pdf_verifier.py (planned artifact), backend/tests/unit/test_pdf_verifier.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Document Automation Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_pdf_verifier.py` (empty PDF, unextractable text, valid multi-page document).
- **Documentation Updates:** docs/modules/document_automation.md (planned artifact).
- **Acceptance Criteria:** Generates formatted multi-page PDF reports; zero-text PDFs or corrupted fonts fail verification; extractable text matches input data.

### Task P10-T005: Artifact Storage Manager and Delivery API

- **Task ID:** P10-T005
- **Task Name:** Implement S3 Artifact Persistence and Download Endpoint
- **Detailed Description:** Implement `ArtifactStorageService` in backend/app/services/artifact_service.py (planned artifact). Stream verified files to S3/MinIO bucket `maya-artifacts/{workspace_id}/{task_id}/{file_name}`; calculate SHA-256 hash and byte size; record record in `file_artifacts` table with `verification_status` and `verification_report`. Implement `GET /api/v1/artifacts/{id}` with JWT authentication and RLS check, returning file stream with appropriate MIME type.
- **Expected Files:** backend/app/services/artifact_service.py (planned artifact), backend/app/api/v1/artifacts.py (planned artifact), backend/tests/integration/test_artifacts_api.py (planned artifact).
- **Dependencies:** P10-T001 through P10-T004.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_artifacts_api.py`.
- **Documentation Updates:** docs/api/ARTIFACTS_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Files upload to S3; records persist in `file_artifacts`; cross-tenant artifact download returns 404/403; SHA-256 matches downloaded file exactly.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/automation/__init__.py (planned artifact)
  - backend/app/automation/xlsx_engine.py (planned artifact)
  - backend/app/automation/docx_engine.py (planned artifact)
  - backend/app/automation/pptx_engine.py (planned artifact)
  - backend/app/automation/pdf_engine.py (planned artifact)
  - backend/app/automation/verifiers/__init__.py (planned artifact)
  - backend/app/automation/verifiers/xlsx_verifier.py (planned artifact)
  - backend/app/automation/verifiers/docx_verifier.py (planned artifact)
  - backend/app/automation/verifiers/pptx_verifier.py (planned artifact)
  - backend/app/automation/verifiers/pdf_verifier.py (planned artifact)
  - backend/app/services/artifact_service.py (planned artifact)
  - backend/app/api/v1/artifacts.py (planned artifact)
  - backend/tests/unit/test_xlsx_verifier.py (planned artifact)
  - backend/tests/unit/test_docx_verifier.py (planned artifact)
  - backend/tests/unit/test_pptx_verifier.py (planned artifact)
  - backend/tests/unit/test_pdf_verifier.py (planned artifact)
  - backend/tests/integration/test_artifacts_api.py (planned artifact)
  - docs/modules/document_automation.md (planned artifact)
  - docs/api/ARTIFACTS_SPECIFICATION.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/automation/`, `backend/app/api/v1/`, `docs/modules/`.

---

## F. API and Data Contracts

### 1. Artifact Retrieval Contract

- **`GET /api/v1/artifacts/{id}`**
  - Headers: `Authorization: Bearer <jwt>`
  - Response (`200 OK`): File binary stream
  - Response Headers:
    ```
    Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    Content-Disposition: attachment; filename="invoice_summary_q3.xlsx"
    X-Artifact-Sha256: 8f4e2b0...
    X-Verification-Status: verified
    ```

### 2. Verification Report Schema

```python
class VerificationReport(BaseModel):
    file_type: Literal["xlsx", "docx", "pptx", "pdf"]
    status: Literal["verified", "failed"]
    sha256: str
    size_bytes: int
    checks_passed: list[str]
    errors: list[str] = []
    metadata: dict[str, Any] = {}
```

---

## G. Security Requirements

- **Tenant Isolation:** Artifact storage paths in S3 and database rows in `file_artifacts` are strictly partitioned by `workspace_id`.
- **File Bomb & Resource Defense:** Maximum file generation size capped at 50 MB; parsing untrusted XML enforces `defusedxml` or `lxml` flags disabling entity resolution (`resolve_entities=False`).
- **No Malicious Scripts:** Excel files generated cannot contain macros (`.xlsm` is banned; only `.xlsx` allowed).
- **Default Status:** Database column `verification_status` defaults to `'pending'`; only the verifier service can set `'verified'`.

---

## H. Testing Strategy

- **Verifier Negative Tests:** Craft intentionally broken test fixtures (Excel sheet with `#REF!`, PowerPoint slide with overflowing text box, 0-page PDF) and assert that every verifier marks them `failed`.
- **Verifier Positive Tests:** Run generation engines across Workflows 5, 6, and 7; assert all generated deliverables receive `verified` status.
- **Cross-Tenant Download Tests:** Attempt to download Tenant A's artifact using Tenant B's JWT; assert HTTP 404 or 403.

---

## I. Definition of Done

1. XLSX, DOCX, PPTX, and PDF generators produce valid deliverables.
2. Programmatic verifiers validate mathematical formulas, XML schemas, text overflow, and font embedding.
3. Negative test suite proves corrupt files are rejected with status `failed`.
4. Artifacts upload to S3/MinIO with recorded SHA-256 hashes.
5. Workflows 5, 6, and 7 execute cleanly and output verified deliverables.
6. Documentation in docs/modules/document_automation.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/unit/test_*_verifier.py` passes 100%; Workflows 5, 6, and 7 generate verified documents with valid verification reports stored in `file_artifacts`.
- **Dependent Phase Unlocked:** Phase 11 (Memory and Knowledge System) and Phase 14 (Frontend Experience) are cleared to begin.
