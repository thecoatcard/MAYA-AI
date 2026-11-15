# File access model: connected folder and uploads

- **Status:** Implemented locally (ADR-0022, Proposed). Tested with real temporary directories and fake models. Not tested against real Windows junctions, a real Electron folder dialog or the real model.
- **Code:** `backend/app/files/` (`safepath.py`, `folder.py`, `uploads.py`, `extract.py`, `access.py`), `backend/app/api/v1/files.py`, `backend/app/mcp/file_tools.py`, `backend/app/automation/file_edit.py`, `backend/app/agents/specialized/project_writer.py`.
- **Contract:** [Files API](../api/FILES.md). **Decision:** [ADR-0022](../adr/0022-connected-folder-and-uploads.md).

## Model

MAYA may read and write only inside ONE folder the user connected, and only through the paths below. The folder path is the only place a client names a location on the machine; everything else is a relative name checked by SafePath. Uploads are stored under MAYA's artifacts directory by id. Nothing outside those two places is ever touched. There is no delete, move or rename capability. Replacing an original file needs a human approval in the web UI (single-use token; voice can never approve) and always keeps a backup.

## Threats and controls

| Threat | Control | Test |
| :-- | :-- | :-- |
| Path traversal (`..`, absolute, drive letter, UNC, NUL, Unicode lookalikes) in a tool argument, an API path or a model-chosen file name | `resolve_inside` rejects them before any disk access; fullwidth dots are normalised first | `test_files_safepath_folder.py`, `test_files_tools.py`, `test_project_writer.py`, `test_files_api.py` |
| Windows reserved names, trailing dot or space, over-long paths | Rejected per segment and by total length (240) | `test_files_safepath_folder.py` |
| Symlink or junction that points outside the folder | Each existing parent and the result are resolved and must stay inside; tree and list skip links that leave | `test_files_safepath_folder.py` (injected resolver and a real link when the OS allows), `test_files_api.py`, `test_files_tools.py` |
| Connecting a dangerous folder (drive root, `C:\Windows`, `/etc`, the user profile root, MAYA's own directories) | `FolderService.validate` refuses with a plain reason; a stored path that later became forbidden is not usable | `test_files_safepath_folder.py`, `test_files_api.py` |
| The model or a task choosing another folder or another user | The folder and user come from the tool-call scope and the user's settings, never from arguments; no argument names a folder | `test_files_tools.py` |
| Silent loss of the user's work | New files use exclusive creation; edits update in place atomically via temp file with a bounded backup in `.maya-backups`; replacing with a candidate needs approval and backs up to `.maya-backups`; the swap is atomic and idempotent | `test_files_extract_edit.py`, `test_files_tools.py` |
| Huge files and floods | 25 MB per file, 60 MB per request, 10 files, 2 MB text read, 5 MB per written file, 300 files per task, bounded prompt views (8,000 characters each) | `test_files_api.py`, `test_files_tools.py`, `test_chat_attachments.py` |
| Malicious file types (executables, archives, scripts, type confusion, zip bombs) | Extension allow-list, blocked list, magic-byte sniffing, Office package checks, uncompressed-size ceiling; downloads are `attachment` with `nosniff` and `CSP: sandbox` | `test_files_api.py` |
| Prompt injection from file contents | Extracted text is bounded, labelled untrusted DATA in every prompt (chat, edit step, project step), never added to instructions; a file cannot add tools, change tiers or approve anything | `test_chat_attachments.py`, `test_files_extract_edit.py` |
| Spreadsheet formula injection (DDE, `HYPERLINK`, `WEBSERVICE`, URLs, external references) | Formulas with those patterns are refused when an edit is applied; a value starting with `=` must be sent as a formula | `test_files_extract_edit.py` |
| Another tenant's upload | Lookup by workspace and canonical UUID; another workspace's id answers 404 (chat: 422) | `test_files_api.py`, `test_chat_attachments.py` |
| A generated website that loads remote code | Validation rejects external scripts and stylesheets unless the user asked for such a stack; nothing is executed | `test_project_writer.py` |
| Folder disappears or becomes read-only | Status is recomputed on read; output falls back to the artifacts store with a warning | `test_files_planning_routing.py` |

## Residual risk

- The API runs on the user's machine (ADR-0013); on a shared server the folder feature must be disabled or restricted to a per-tenant root.
- A user-approved replacement is still a replacement: the backup is the recovery path.
- Windows junction behaviour is covered by the resolution logic and an injected resolver, not by a real junction test.
