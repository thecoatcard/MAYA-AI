# Change Report

Status: **Implemented; partially tested.**

## Summary

The PostgreSQL LangGraph checkpointer now binds every saver operation to a server-supplied workspace and user identity
through a dedicated connection; Alembic revision `0006` adds owner-aware forced RLS and prevents ownerless legacy rows
from becoming visible. Runtime checkpointer construction no longer calls schema setup. The Windows Selector policy is
now installed and verified at the synchronous `python -m app` entrypoint before Uvicorn starts, and incompatible late
entrypoints fail explicitly. No database was contacted and no migration was applied.

## Motivation

The existing checkpoint tables had no tenant owner columns or RLS, and runtime setup could attempt schema creation.
This closes the checkpoint-specific tenant-isolation gap without guessing ownership for existing data.

## Files Changed

| File | Change | Status |
| :--- | :--- | :--- |
| `backend/app/agents/checkpointer.py` | Require workspace/user UUIDs, set connection-local owner context, remove runtime `setup()`, and validate Windows selector-loop setup/use | Implemented |
| `backend/app/__main__.py` | Configure/verify Windows selector policy before `uvicorn.run` starts the loop | Implemented |
| `backend/app/db/migrations/versions/0006_checkpoint_owner_isolation.py` | Add compatible checkpoint tables/columns, owner FK, grants, indexes and forced RLS; preserve ownerless rows | Implemented; not applied |
| `backend/tests/unit/test_checkpointer_owner.py` | Test owner context, invalid identities, selector policy timing/runtime validation, migration RLS contract and guarded downgrade without a database | Tested |
| `backend/tests/integration/test_langgraph_checkpointer.py` | Add PostgreSQL owner-isolation coverage for same-workspace users and another workspace | Added; not run |
| `backend/tests/postgres/test_migrations.py` | Include checkpoint schema/RLS and legacy-row quarantine in database tests | Added; not run |
| `backend/tests/unit/test_transport_guards.py` | Assert event-loop policy setup precedes `uvicorn.run` | Tested |
| `backend/app/agents/README.md` | Document owner-bound saver and implementation status | Updated |
| `backend/app/db/README.md` | Document checkpoint schema ownership boundary | Updated |
| `docs/architecture/DATA_MODEL.md` | Document checkpoint tables, ownership, migration and rollback constraints | Updated |
| `docs/architecture/ORCHESTRATOR_SPECIFICATION.md` | Document server-owned identity and Alembic-only setup | Updated |
| `docs/security/AUTHENTICATION.md` | Document checkpoint trust boundary and unverified live test status | Updated |
| `docs/adr/0010-checkpoint-owner-isolation.md` | Record the proposed owner-binding boundary | Added |
| `docs/adr/README.md` | Index ADR-0010 | Updated |

`CHANGELOG.md` was intentionally not edited, as requested. Other pre-existing worktree changes were preserved.

## Architecture Impact

Checkpoint storage is keyed by workspace and user at the database policy boundary, with a composite owner reference to
`users`. Each saver owns a dedicated Psycopg connection so one identity context is used for all of its reads and writes.
The orchestrator remains unwired to production routes. ADR-0010 is Proposed, not Accepted.

## Security Impact

`checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` have forced RLS requiring both owner settings. Server-side
authentication must supply the UUIDs; graph state and thread IDs are not identity sources. `maya_app` cannot create the
schema. Existing rows without trustworthy owner data remain NULL and inaccessible. Arbitrary SQL execution as
`maya_app` can still alter custom settings, so this is not a defense against a compromised runtime connection.

## Tests Added or Updated

- Offline unit coverage verifies the connection receives both owner identities, invalid identity types fail before
  connect, runtime setup is not called, migration SQL enables the owner policy/FK, and downgrade is guarded.
- PostgreSQL tests cover same-workspace user isolation, cross-workspace isolation, schema/RLS flags, and preservation of
  ownerless legacy rows. These tests were authored but **not run**.

## Validation Results

| Command | Result |
| :--- | :--- |
| `Set-Location backend; .venv/Scripts/python.exe -m pytest tests/unit/test_checkpointer_owner.py tests/unit/test_transport_guards.py::test_the_server_is_started_without_trusting_forwarded_headers tests/integration/test_langgraph_checkpointer.py::test_memory_checkpointer_storage_and_retrieval` | **Passed:** 9 tests, including deterministic policy setup-before-Uvicorn, install-before-loop, late-call rejection, and active-loop validation. The late-call test first failed against the old implementation, then passed after the fix. |
| Focused `ruff check` and `ruff format --check` on `app/agents/checkpointer.py`, `app/__main__.py`, `tests/unit/test_checkpointer_owner.py`, and `tests/unit/test_transport_guards.py` | **Passed** |
| Focused `mypy` on the same four Python files | **Passed** |
| `npm run docs:check` (prior run) | Raw output reported 151 Markdown files passed. **Reclassified: potentially unsafe and non-independent for ignored/symlink scope.** Inspection of `check-docs.mjs` and `fsutil.mjs` showed configured Markdown roots are walked without Git ignored-file filtering (although symlink entries are skipped); link-target existence and Markdown reads can follow symlinks. The raw output does not establish whether ignored Markdown or symlink targets were read. Not rerun in this shared workspace per follow-up request. |
| `Set-Location backend; .venv/Scripts/python.exe -m alembic history` | **Passed:** revision chain `0005 -> 0006` shown; no DB connection |
| `npm run validate:fast` | **Failed overall on existing worktree findings:** repository secret scan flagged nine potential secrets without printing values; global `ruff`/`mypy` commands were unavailable on PATH; web formatting reported existing `README.md` and `ChatWorkspace.test.tsx` changes |
| Full backend `ruff check .` | **Passed** |
| Full backend `ruff format --check .` | **Failed on five unrelated pre-existing files** under `app/gateway`, `app/services`, and `tests/gateway` |
| Full backend `mypy .` | **Failed on an unrelated existing type error at `tests/gateway/test_budget_atomic_admission.py:326`** |
| PostgreSQL migration/RLS tests and `npm run validate` | **Not run:** would risk contacting a database, which was explicitly prohibited |

## Documentation Updated

Updated the database, architecture, orchestrator, security, and checkpointer entrypoint documentation; added Proposed
ADR-0010 and this report. The earlier documentation-check output is not treated as independent assurance for ignored or
symlink targets; the checker was not rerun in the shared workspace. `CHANGELOG.md` remains unchanged per request.

## Database / API Impact

Revision `0006` is additive. It creates the expected saver tables if absent; if legacy tables exist, it adds nullable
owner columns without backfilling existing rows, adds owner defaults for new writes, and adds forced RLS. Downgrade
refuses to proceed once any checkpoint data exists. The `create_postgres_checkpointer` call contract now requires
`workspace_id` and `user_id`. The migration was not applied and no endpoint was changed.

## Risks and Limitations

- No cross-tenant live PostgreSQL verification is claimed.
- Legacy checkpoint rows cannot be resumed until a separately reviewed, evidence-based recovery is approved.
- Apply the migration with old checkpoint writers stopped; index creation and table DDL may block access depending on
  table size.
- Rollback after checkpoint data exists is intentionally refused; prefer a forward fix or a separately planned recovery
  over removing owner RLS.
- The full repository fast validation remains red on unrelated existing worktree findings.
- Windows event-loop behavior was tested deterministically with mocked policy/loop classes, not on a Windows runtime.
- The prior docs-check raw output may include ignored Markdown and could follow symlink targets referenced by links; its
  coverage of shared-workspace ignored/symlink content is unverified.

## Approval Required

The current user request explicitly approved the Phase 5 checkpoint tenant-isolation repair, including the additive
database migration if safe. That approval covered changing the checkpoint schema/RLS and the runtime owner-binding
contract. The migration was not executed; no database or external service was contacted.

## Remaining Work

The prior `uv run --offline --directory backend python -c ...` inspection command failed during dependency resolution.
`backend/pyproject.toml` was already modified in the initial worktree; `backend/uv.lock` was initially clean but appeared
modified later. Neither file was intentionally edited. The failed `uv run` may have triggered a lock update; attribution
is not certain. Both files were left untouched after the follow-up provenance request.

Run the PostgreSQL migration and isolation tests in an explicitly authorized local test environment; apply revision
`0006` through the migration process after an operational review; integrate the owner-bound checkpointer into a
production route only after verified identity handling and thread-ID generation are designed. The follow-up fix moves
Windows policy setup to the `python -m app` entrypoint before Uvicorn starts and fails loudly when invoked too late or
when the active loop is incompatible; see the focused checks above.
