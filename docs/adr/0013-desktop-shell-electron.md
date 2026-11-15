# ADR-0013: Desktop shell with Electron

- **Status:** Accepted (owner approved on 2026-11-08)
- **Date:** 2026-11-08
- **Deciders:** Project owner (approved in conversation), software architect
- **Related:** [ADR-0009](0009-refresh-cookie-transport.md), [ADR-0012](0012-in-process-task-queue.md), [ADR-0014](0014-local-owner-bootstrap.md), `apps/desktop/README.md`; overrides `MAYA_AI_FINAL_PLAN.md` section 3 (out of scope), section 18 (repository structure), section 22 (deployment), section 26 item 5 and open decision O4

## Context

The plan listed a Tauri desktop shell as post-MVP and left open decision O4 (cloud or local desktop first) on "cloud". The owner now wants a desktop app and approved Electron. The backend already supports it: `python -m app.desktop` binds loopback, enables the local-owner bootstrap (ADR-0014), serves the exported web app from `MAYA_WEB_STATIC_DIR`, and exits when its stdin closes. The task worker runs inside the API process (ADR-0012), so there is one application process to supervise plus Docker Compose for Postgres and Redis. Tauri would need a Rust and MSVC toolchain this managed Windows machine does not have.

## Decision

1. Build `apps/desktop`, a plain-ESM Electron shell (no TypeScript build step, tests with `node --test`). Exact pins `electron@44.9.3` and `electron-builder@26.18.0`, installed only inside `apps/desktop`.
2. The web UI is a static export (`MAYA_DESKTOP_EXPORT=1`, directory `apps/web/.next-desktop`) served by the API itself. The window loads `http://127.0.0.1:<port>/`, so the UI, the HttpOnly refresh cookie (ADR-0009), SSE and the voice WebSocket stay same-origin with no CORS and no proxy.
3. A pure `supervisor.mjs` runs startup gates in order: Docker engine reachable, `docker compose up -d --wait --no-recreate postgres redis` (MAYA services only), a read-only Alembic `current` versus `heads` check, then the API, then `/api/v1/healthz` and `/api/v1/readyz`. If the database is behind, a native dialog asks for confirmation and `alembic upgrade head` runs only after the user agrees. This is the only place a migration runs automatically, and only on explicit consent; the dev rule "never migrate silently" still holds.
4. Shutdown closes the API's stdin, waits 12 s, then `taskkill /T /F`. Postgres and Redis containers keep running and are never removed (no `down`, no volume removal). A crash restarts the API at most 3 times per 60 s with backoff, then shows a failed page with the log path.
5. Security: context isolation, sandbox, no Node integration, web security on; navigation limited to the API origin; new windows are denied and only `https:` links open in the system browser; microphone (`media`, audio) is granted only to the app origin and every other permission is denied; the preload exposes only `isDesktop`, `platform` and `version`, with no IPC. Child processes get an allow-listed environment, and the shell passes the env file path on but never reads or logs its contents.
6. Env file: `MAYA_ENV_FILE`, else the repo-root `.env` if present, else `.env.example`.

## Alternatives considered

- **Tauri 2 (the plan's choice):** smaller shell, but needs Rust and MSVC, Rust supervisor code, and extra work for the WebView2 microphone prompt and sidecar tree-kill. Revisit later; the supervisor contract (spawn, env, stdin EOF, health polling) is framework-neutral.
- **Next server inside Electron:** rewrite target is baked at build time and the voice hook breaks off port 3000. Kept only as a fallback.
- **Custom `app://` protocol:** makes the API cross-site, which breaks the `SameSite=Strict` cookie of ADR-0009.

## Consequences

- Positive: one extra process model, no CORS or cookie redesign, plain JS that reviewers already read, same Chromium as development.
- Negative: Docker Desktop is a hard prerequisite (and its licence terms apply); the installer is unsigned (SmartScreen warning) and has no auto-update; Electron security patches must be tracked by us.
- Known gaps: the Python runtime and backend source are not bundled, so packaged builds need `MAYA_REPO_ROOT` (a checkout with `backend/.venv`) or `MAYA_PYTHON`; no tray or global shortcut; no CI or root-script wiring (root `package.json`, workflows and governance config were not approved to change); the Windows microphone prompt and a visible window were not verified by a human.
- Mitigations: unit tests cover every policy and the supervisor state machine; `npm --prefix apps/desktop run smoke` exercises the real stack against a throwaway database.
- ADR-0009's review trigger ("browser stops using the same-origin proxy") is noted: the origin is still the same, only the server behind it is now the API.
