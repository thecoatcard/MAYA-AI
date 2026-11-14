# ADR-0022: Connected folder, uploaded files, file editing and project writing

- **Status:** Proposed (owner request, 2026-11-10)
- **Date:** 2026-11-10
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0013](0013-desktop-shell-electron.md), [ADR-0011](0011-mcp-gateway-and-specialist-agents.md), [Files API](../api/FILES.md), [Agent request flow](../architecture/AGENT_REQUEST_FLOW.md#10-connected-folder-uploads-and-project-writing-adr-0022), [Tool permissions](../../governance/TOOL_PERMISSIONS.md)

## Context

Results lived only in MAYA's artifacts store and users could not hand MAYA a file. The owner asked for (1) ONE connected local folder where tasks save their output (documents in its root, a website in a new project subfolder), (2) attached files that MAYA can read, summarise, convert and edit, (3) no overwriting of the user's files without approval, (4) no deleting, and (5) no code execution: a website is a set of files, not a build.

## Decision

1. **One folder per user, in existing storage.** The absolute path is stored in the user's settings JSON (`workspace_folder`). Validation: absolute, exists, is a directory, writable (a temporary file is created and removed), not a drive or filesystem root, not an operating-system directory, not the user profile root itself, not inside MAYA's repository, storage or artifacts directories. Status is recomputed on every read (`none`, `ok`, `missing`, `not_writable`).
2. **One path gate.** Everything that touches the folder goes through `SafePath.resolve_inside` (rejects absolute, drive, UNC, `..`, NUL, reserved names, trailing dot or space, over-long paths and Unicode tricks; resolves each existing parent and the result and requires containment, which rejects symlinks and junctions; forbids MAYA's own directories).
3. **Uploads on disk, no migration.** `<artifacts>/<workspace_id>/uploads/<id>/<safe name>` plus a JSON manifest; per-workspace isolation by directory and by id lookup; size, type and magic-byte checks; executables and archives refused.
4. **In-place updates with bounded backup.** New files use exclusive creation. An edit updates an existing file in place atomically (temporary file in same folder, then `os.replace`), keeping the file name and extension. Before overwriting, a bounded backup of the previous version is kept in `.maya-backups` (pruning oldest, maximum 5 backups per file), and the response mentions where the previous version is kept so the user can restore it. If the user explicitly asks for a separate copy ('save as', 'make a copy'), a copy is created with a clear name. Replacing a file with a candidate is supported via `file_replace_original` with an approval diff, UI-only approval, and backup preservation. There is no delete, move or rename tool.
5. **The model edits through structured changes.** It never gets a path or a handle: text files return the new content, docx paragraph edits, xlsx cell operations, pptx shape text. Code applies them (python-docx, openpyxl, python-pptx), verifies that the result reopens, and builds a diff. PDFs and images are not editable (stated honestly); a new PDF can be created from extracted text.
6. **Project writer, no execution.** Plan call, one generation call per file, validation in code (HTML well formed, local links and assets exist, CSS and JS not empty, no external script or stylesheet unless the user asked for such a stack), then SafePath writes into `<folder>/<slug>/`. No npm, no shell, no browser run. The `coding` agent stays disabled.
7. **Roles without a migration.** `file_edit` and `project` are planner roles stored with the existing `file` role plus `input_data.executor`, like the text `executor`; the `assigned_agent` constraint is untouched.
8. **Output routing.** `OutputTarget.resolve(workspace, user)` returns the folder or the artifacts store. A missing or unwritable folder falls back to the store with a warning in the result; a result is never lost. Artifacts are registered with the real path so download works.
9. **File content is data.** Extracted text is bounded, labelled untrusted in every prompt and never treated as instructions; a file cannot add tools, change tiers or approve anything.

## Consequences

- Tasks started by voice use the folder too: agents resolve it from the caller's workspace and user when they run.
- The folder path is a user-chosen location on the machine that runs the API. This fits the desktop deployment (ADR-0013, the API runs on the user's machine). For a hosted multi-user server this feature must be disabled or the folder restricted to a per-tenant root; it is not designed for that.
- Windows junction behaviour is checked by resolution in code and by tests with an injected resolver, not against real junctions.
- New settings (lowering only): `MAYA_UPLOAD_MAX_BYTES` (default 25 MB), `MAYA_UPLOAD_TOTAL_MAX_BYTES` (default 60 MB).
- No schema change, no migration, no new dependency.

## Alternatives

- **Several folders or a whole drive.** Larger blast radius and no clear owner benefit; one folder is easy to reason about and to show in the UI.
- **Silent overwrite without backups.** Dangerous because a wrong edit would destroy work; updating in place with bounded backups in .maya-backups and honest failure reporting keeps the user's data safe and recoverable.
- **A real shell or build for websites.** Needs execution, sandboxing and dependency installs; deliberately excluded (the owner asked for no code execution).
- **A database table for uploads and a column for the folder.** Cleaner queries but needs a migration; the manifest and the settings JSON are enough at this size and can move to tables later.
