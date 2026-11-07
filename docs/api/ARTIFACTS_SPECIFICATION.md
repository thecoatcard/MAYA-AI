# Artifacts Delivery API Specification

- **Base Path:** `/api/v1/artifacts`
- **Authentication:** Bearer JWT required on all endpoints
- **Authorization:** PostgreSQL Row-Level Security (RLS) workspace isolation

---

## Endpoints

### 1. Download Artifact Binary

- **Endpoint:** `GET /api/v1/artifacts/{artifact_id}`
- **Security:** Requires valid JWT for the workspace owning the artifact.
- **Success Response:** `200 OK` (binary stream)
  - **Headers:**
    - `Content-Type`: standard MIME type (e.g. `application/pdf`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)
    - `Content-Disposition`: `attachment; filename="{file_name}"`
    - `X-Artifact-Sha256`: Hexadecimal SHA-256 hash of the artifact file
    - `X-Verification-Status`: Verification status (`verified`, `failed`, `pending`)
- **Error Responses:**
  - `401 Unauthorized`: Missing or invalid JWT.
  - `404 Not Found`: Artifact not found or belongs to another workspace (`{"error": "artifact_not_found"}`).

### 2. Get Artifact Metadata

- **Endpoint:** `GET /api/v1/artifacts/{artifact_id}/metadata`
- **Success Response:** `200 OK`
  ```json
  {
    "id": "e4b2d3c1-...",
    "workspace_id": "a1b2c3d4-...",
    "task_id": "f5e6d7c8-...",
    "user_id": "11223344-...",
    "file_name": "quarterly_financials.xlsx",
    "file_type": "xlsx",
    "size_bytes": 14520,
    "sha256": "8f4e2b0...",
    "verification_status": "verified",
    "verification_report": {
      "file_type": "xlsx",
      "status": "verified",
      "sha256": "8f4e2b0...",
      "size_bytes": 14520,
      "checks_passed": [
        "file_size_within_budget",
        "valid_file_extension",
        "workbook_has_worksheets",
        "zero_formula_errors",
        "verified_1_formulas"
      ],
      "errors": [],
      "metadata": {
        "total_sheets": 1,
        "total_cells_checked": 24,
        "formulas_checked": 1
      }
    },
    "created_at": "2026-11-07T10:00:00Z"
  }
  ```

### 3. List Workspace Artifacts

- **Endpoint:** `GET /api/v1/artifacts`
- **Query Parameters:**
  - `task_id` (optional UUID): Filter artifacts produced by a specific task.
- **Success Response:** `200 OK` with JSON array of `ArtifactMetadataResponse` objects.
