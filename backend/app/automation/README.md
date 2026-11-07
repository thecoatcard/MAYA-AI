# Document Generation & Automation Module (`app.automation`)

The `app.automation` module provides Phase 10 document generation engines and deterministic verifiers for MAYA AI.

## Architecture

This package implements generation and verification engines for office and presentation formats:

- `xlsx_engine.py`: Multi-tab workbook creation, column formatting, frozen panes, borders, formulas.
- `docx_engine.py`: Document generation, heading hierarchy, callout blocks, styled tables.
- `pptx_engine.py`: 16:9 widescreen presentations, title banners, metric cards, canvas bounding boxes.
- `pdf_engine.py`: PDF generation with two-pass `NumberedCanvas` page numbering and running headers.
- `verifiers/`: Deterministic quality, structure, and security verifiers for generated artifacts:
  - `xlsx_verifier.py`: Detects formula errors (`#REF!`, `#DIV/0!`), enforces `.xlsx` (prohibiting macro-enabled `.xlsm`), checks parenthesis balance.
  - `docx_verifier.py`: XXE-safe XML parsing (`resolve_entities=False`), heading order validation, table column count validation.
  - `pptx_verifier.py`: Validates slide count, 16:9 aspect ratio, and shape bounding-box canvas boundary compliance.
  - `pdf_verifier.py`: Asserts page count thresholds, non-empty text extraction layers via `pypdf`.
  - `schemas.py`: Standardized `VerificationReport` model and SHA-256 computation.

## Integration

Engines and verifiers are orchestrated by `app.services.artifact_service.ArtifactService` and served via `/api/v1/artifacts`.
For complete architecture and specification details, see [Document Automation Documentation](../../../docs/modules/document_automation.md) and [Artifacts Specification](../../../docs/api/ARTIFACTS_SPECIFICATION.md).
