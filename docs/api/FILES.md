# Files API: connected folder and uploaded files

Implemented in `backend/app/api/v1/files.py` and `backend/app/files/` (ADR-0022, Proposed). Every route is under `/api/v1/files`, bearer-authenticated like the rest of the API, scoped to the caller's workspace (the folder to the user as well), and answers with `Cache-Control: no-store`. Errors use `{"error": <code>, "message": <plain words>}` (plus `reason` or `file` where stated).

## Connected folder

The user can connect ONE local folder. MAYA reads and writes only inside it. The path is stored in the user's settings JSON (`user_settings`, key `workspace_folder`); there is no migration. Status is recomputed from the disk on every read.

| Method and path | Body or query | Success | Errors |
| :-- | :-- | :-- | :-- |
| `GET /folder` | | `{path, status, writable, name}`; `status` is `none`, `ok`, `missing` or `not_writable` | |
| `PUT /folder` | `{"path": "<absolute path>"}` | the same object | 422 `invalid_folder` with `message` and `reason` |
| `DELETE /folder` | | 204 (disconnects; the files stay) | |
| `GET /folder/tree` | `path` (relative, default root), `limit` (1 to 1000, default 200) | `{path, entries: [{name, type: "file"|"dir", size, modified_at}], truncated}` | 409 `no_folder`, 400 `invalid_path`, 404 `not_found` |

`PUT /folder` reasons (`reason`): `empty`, `bad_path`, `unc`, `not_absolute`, `missing`, `not_directory`, `root` (a drive or filesystem root), `user_profile` (the user profile root itself), `system_dir` (Windows, Program Files, `/`, `/etc`, `/usr`, `/System`, ...), `maya_dir` (MAYA's repository, storage or artifacts directory), `readonly` (a temporary file could not be created and removed there).

`GET /folder/tree` lists names only (no content), never lists a link that leaves the folder, and hides MAYA's own write-probe files.

## Uploads

Bytes live on disk under `<artifacts>/<workspace_id>/uploads/<upload_id>/<safe name>` with a `manifest.json` beside them. A client never chooses a path; the id is a canonical UUID and anything else is a 404.

| Method and path | Notes |
| :-- | :-- |
| `POST /uploads` | multipart field `files`, 1 to 10 files, each at most 25 MB, together at most 60 MB (`MAYA_UPLOAD_MAX_BYTES`, `MAYA_UPLOAD_TOTAL_MAX_BYTES` may lower these). 201 with `[{id, name, size, kind, created_at}]`. All files are validated before any is saved. |
| `GET /uploads?limit=&cursor=` | **Deviation from the first contract draft:** returns `{"items": [...], "next_cursor": string or null}` (not a bare array) so paging works. `limit` 1 to 100, default 50. |
| `GET /uploads/{id}` | metadata, 404 `upload_not_found` for an unknown id or another workspace's id |
| `GET /uploads/{id}/download` | authenticated blob; `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, `X-Upload-Sha256` |
| `DELETE /uploads/{id}` | 204 |

`kind` is one of `text`, `code`, `markdown`, `csv`, `json`, `docx`, `xlsx`, `pptx`, `pdf`, `image`, `other`.

Allowed extensions: txt md csv json html css js ts tsx jsx py java c cpp cs go rs sql yaml yml xml toml ini log, docx xlsx pptx pdf, png jpg jpeg gif webp svg. Rejections, all 415 unless noted: `blocked_type` (exe dll bat cmd ps1 sh msi zip 7z rar jar and similar), `unsupported_type`, `executable_content` (MZ, ELF or Mach-O magic bytes under any name), `archive_content`, `type_mismatch` (the content does not match the extension: a text file with NUL bytes, a `.png` that is not a PNG, a `.docx` that is not an Office package with its main part), `empty_file` (400), `file_too_large` (413), `total_too_large` (413), `payload_too_large` (413, the whole request), `file_count` (400), `bad_multipart` (400). Names are sanitised (path parts, reserved characters and Windows reserved names removed, 120 characters at most).

## Chat

`POST /api/v1/chat/message` accepts an optional `attachments: ["<upload_id>", ...]` (at most 10). An unknown id, another workspace's id or more than 10 answers 422 with `fields: ["attachments"]` and nothing is stored. The model receives, after the user's text, a bounded view of each file (the first 8,000 characters of extracted text, 24,000 in total; sheet previews; slide texts; images and unreadable or scanned PDFs are noted as such, with no pretence of reading pixels) framed as untrusted DATA. The stored user message keeps only the user's text; its metadata records `attachments: [{id, name, kind}]` so the thread shows chips after a reload. The `submit_task` tool gains `input_files` (ids attached to this message; with none named, all attached files are passed). An id that was not attached to the message is refused and no task is started.

## Tasks

`POST /api/v1/tasks` accepts `input_files: [upload_id]` (at most 10; an unknown id answers 422 `attachment_not_found`). Every planned step's `input_data` carries `input_files` and `output_dir_mode: "folder" | "artifacts"` (informational: agents resolve the folder again when they run, so a folder connected or removed after submission, and tasks started by voice, behave the same). Attached files reach every step as untrusted dependency data (`attachment:<id>` outputs).

Planner roles added: `file_edit` and `project`. They are stored with the existing `file` role and `input_data.executor` set to `file_edit` or `project` (the `assigned_agent` database constraint allows only the original roles, so there is no migration).

## Where results go

- A document (pdf, pptx, docx, xlsx, md) is built and verified in the artifacts store, then copied into the root of the connected folder under a collision-safe name (`report.pdf`, then `report (2).pdf`, ...); nothing is ever overwritten. The artifact is registered with the real path, so download still works.
- A website is written to `<folder>/<project-slug>/` (`solar-energy-site`, then `solar-energy-site-2`); every file is registered as an artifact.
- No folder connected: results stay in the artifacts store and the step text says so. Folder missing or not writable: the result is saved in the artifacts store and the step text carries a warning. A result is never lost because the folder failed.

## Editing

An edit on an existing file writes back to the SAME path in place atomically (temporary file in same folder, then `os.replace`), keeping the file name and extension. A bounded backup of the previous version is kept in `.maya-backups` (pruning oldest, maximum 5 backups per file), and the response mentions where the previous version is kept so the user can restore it. If the user explicitly asks for a copy ('save as', 'make a copy'), a separate copy is created. `file_edit` returns a unified text diff (`result.diff`) and a change count. The model never touches the file directly: it returns a structured change that code applies.

| Kind | Model returns | Applied with |
| :-- | :-- | :-- |
| text, code, markdown, csv, json | `new_content` (complete, at most 200,000 characters) | a plain write |
| docx | `edits: [{paragraph_index, new_text}]`, optional `append_paragraphs` | python-docx, styles preserved |
| xlsx | `ops: [{sheet, cell, text|number|formula}]` | openpyxl (formulas starting with `=`; `WEBSERVICE`, `HYPERLINK`, `|`, `[`, URLs and UNC paths are refused) |
| pptx | `ops: [{slide, shape_index, new_text}]` | python-pptx |
| pdf, images | not editable: the step says so and offers to create a new PDF from the extracted text | |

Replacing an original is a separate step with the tool `file_replace_original` (`APPROVAL_REQUIRED`), args `{"path": "<relative>", "new_version": "<relative>"}`. The worker prepares the approval just before asking: both paths must be real files inside the connected folder, and the payload carries the diff. The approval binds that exact pair, uses the existing single-use token flow and is UI-only (voice can never approve). After approval the new version is copied over the original through an atomic replace; the original is first copied to a backup in `.maya-backups`. Repeating the step is idempotent (same content: `already_replaced`, no second backup).

## Agent tools

Registered in the built-in `maya-tools` server and the permission registry (deny-by-default): `file_list` (READ), `file_read` (READ), `file_write_new` (WRITE, creates a new file, fails if it exists), `file_edit_as_new_version` (WRITE), `file_replace_original` (APPROVAL_REQUIRED). There is no delete, move or rename tool. The workspace and user come from the tool-call scope set by the worker, never from arguments; the folder is read from that user's settings. Every path is relative and goes through SafePath. Limits: reads up to 2 MB of text and 60,000 characters per call, writes at most 5 MB per file and 300 files per task. Every write is audited.

## Path safety (SafePath)

`backend/app/files/safepath.py`. `resolve_inside(root, relative)` rejects: absolute paths, leading `/` or `\`, drive letters (`C:x`), UNC paths, `..` segments, NUL characters, Windows reserved names (`CON`, `NUL`, `COM1`, ...) with or without an extension, segments with a trailing dot or space, `<>:"|?*`, segments over 120 characters and total paths over 240 characters; Unicode is normalised (NFKC) first so fullwidth dots cannot hide a `..`. Each existing parent and the final path are resolved and must stay inside the root, which rejects symlinks and junctions that lead out. MAYA's own directories are forbidden even inside the root. Containment is case-insensitive on Windows.

## Not covered / not verified

Real Windows junction behaviour (symlink tests skip when the OS refuses to create links), a real Electron folder dialog, edit quality with the real model, reading the pixels of images or scanned PDFs (not offered), editing PDFs (not offered), network (UNC) folders (refused).
