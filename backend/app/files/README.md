# app/files: connected folder, uploads, extraction and file edits (ADR-0022)

- `safepath.py`: `resolve_inside` / `split_relative`, the single gate for every path under the connected folder.
- `folder.py`: `FolderService` (validate, get, set, clear, status recomputed on read); the path is stored in `user_settings`.
- `uploads.py`, `multipart.py`: upload storage under `<artifacts>/<workspace>/uploads/<id>/`, sniffing and limits, a small multipart parser.
- `extract.py`: bounded text views (text, code, docx, xlsx, pptx, pdf, images as metadata only).
- `access.py`: `FileContext`, what tools and agent steps may do (read, create new versions).
- `edit_step.py`, `replace_flow.py`: the `file_edit` task step and the approval payload for `file_replace_original`.
- `attachments.py`: bounded untrusted views of chat and task attachments.
- `services.py`: the installed `FileServices`, `OutputTarget` (folder or artifacts store) and collision-safe write helpers.

Contract: `docs/api/FILES.md`. Threats: `docs/security/FILE_ACCESS.md`. Tests: `tests/unit/test_files_*.py`, `tests/integration/test_chat_attachments.py`.
