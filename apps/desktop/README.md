# MAYA desktop shell

Electron shell for MAYA AI on Windows ([ADR-0013](../../docs/adr/0013-desktop-shell-electron.md)). It checks Docker, starts MAYA's Postgres and Redis containers, checks the database schema, starts the local API (`python -m app.desktop`), and opens a window on `http://127.0.0.1:<free port>/`. The API serves the exported web app, so the UI is same-origin with the API.

Status: implemented and smoke-tested headless. Not yet verified by a person with a visible window or a microphone.

## Prerequisites

- Windows, Node.js 22+, Docker Desktop running.
- `backend/.venv` created (see [local development](../../docs/operations/LOCAL_DEVELOPMENT.md)).
- The web export: `npm --prefix apps/desktop run build:web` (writes `apps/web/.next-desktop`).
- `npm --prefix apps/desktop install` (installs Electron 44.9.3 and electron-builder 26.18.0, exact pins).

## Run

```bash
npm --prefix apps/desktop run build:web   # once, and after web changes
npm --prefix apps/desktop run dev         # same as `start`
```

First run: the splash lists the real steps (Docker, database and cache, schema, engine, readiness). If the database is behind, a dialog asks before `alembic upgrade head` runs; "Not now" changes nothing. Then the app opens; on a fresh database the sign-in page offers the local-owner bootstrap ([ADR-0014](../../docs/adr/0014-local-owner-bootstrap.md)).

Env file: `MAYA_ENV_FILE`, else the repo-root `.env`, else `.env.example`. The shell passes the path on and never reads or logs the file.

Other variables: `MAYA_REPO_ROOT` and `MAYA_PYTHON` (packaged builds only).

## Test, smoke, pack

```bash
npm --prefix apps/desktop test        # node --test, no Electron or Docker needed
npm --prefix apps/desktop run smoke   # real stack, hidden window, throwaway database
npm --prefix apps/desktop run pack    # unpacked app in apps/desktop/dist/win-unpacked
npm --prefix apps/desktop run dist    # unsigned NSIS installer in apps/desktop/dist
```

`smoke` creates `maya_desktop_smoke` inside the running `maya-foundation` Postgres container, migrates only that database, runs the shell hidden with example credentials, asserts the page title, the preload bridge, `GET /api/v1/auth/bootstrap/status` (available) and `GET /` (exported HTML), then always drops the database. It never touches the `maya` database. If Docker is down it only reports that.

## Behaviour notes

- Containers are left running on quit and never removed. Compose runs with `--no-recreate`, so existing containers are not replaced.
- The API log is `api.log` (rotated, redacted) under Electron's logs folder (`%APPDATA%\maya-desktop\logs`); the failed page has an "Open logs" button.
- A crashed API restarts up to 3 times per 60 s, then the failed page appears.
- Window size and position are remembered in `window-state.json` under the user data folder.
- Folder bridge (preload, `window.maya`): `pickFolder()` opens the native folder dialog (open directory, create directory, parented to the main window) and resolves the absolute path or `null`; `revealPath(path)` shows an item in the file manager and resolves `true` or `false`. Only the app page on the API origin may call either; `revealPath` accepts only absolute, non-UNC paths inside a folder chosen with `pickFolder` in this session. The web app saves the chosen folder through `PUT /api/v1/files/folder`. Handlers: `src/ipc.mjs`; tests: `test/ipc.test.mjs`.
- Open in the PC's own app (preload, `window.maya.openArtifact({ artifactId, fileName, token })`, channel `maya:open-path`, handler `src/open-file.mjs`, tests `test/open-file.test.mjs`): the renderer never sends a path. The main process accepts only the app page on the API origin, a UUID artifact id, a bounded token and a safe file name; downloads `GET /api/v1/artifacts/{id}` with the bearer token into `%TEMP%\maya-open\<id>\<name>` (emptied at start and at quit) and calls `shell.openPath` on that file only. Refused with `{ ok: false, reason: "blocked-type" }`: names with separators, drive letters, UNC or traversal parts, trailing dots or spaces, Windows device names, no extension, and executable or script types in any extension part (exe, com, scr, bat, cmd, ps1, msi, lnk, js, vbs, hta, jar, dll, reg, and more; see `BLOCKED_EXTENSIONS`). Other reasons: `untrusted`, `bad-request`, `download-failed`, `open-failed`. Context isolation, sandbox and the CSP are unchanged. An opened file is a copy: edits to it are not saved back to MAYA.

## Known gaps

- Python runtime and backend are not bundled: packaged builds need `MAYA_REPO_ROOT` (a checkout with `backend/.venv`) or `MAYA_PYTHON`.
- Unsigned installer (SmartScreen warning), no auto-update, Windows only.
- `npm --prefix apps/desktop run dist` (NSIS) has not been run; `pack` (unpacked) has.
- No root `npm run` scripts or CI step (not approved yet).

## Troubleshooting

- "Docker Desktop is required": start Docker Desktop, wait until it is running, press Retry.
- "Web app export is missing": run `build:web`.
- Port or container problems: `docker compose --env-file .env.example ps`; open the log via the failed page.
- Microphone blocked: enable Windows Settings > Privacy > Microphone > "Let desktop apps access your microphone".
- `SyntaxError: The requested module 'electron' does not provide an export named 'BrowserWindow'`: the shell has `ELECTRON_RUN_AS_NODE` set (VS Code's terminal does), so Electron runs as plain Node. `npm run dev` and `npm start` now go through `scripts/dev.mjs`, which removes that variable before starting Electron. If you start `electron .` yourself, unset it first.

## Scheduled reports in the background (experimental)

`electron . --run-schedules` starts no window: it starts Docker data services and the backend (whose scheduler catches up on due reports), runs `python -m app.schedules.run_due` until nothing is due (at most 25 minutes) and quits. If MAYA is already open it exits at once (the open app runs schedules). Settings > Schedules > "Wake my PC to run schedules" calls `window.maya.registerScheduleWake(true | false)`, which after a confirm dialog creates or removes ONE user-level Windows task named `MAYA AI Scheduler` (at logon and every 30 minutes, only while you are logged on; no admin rights, no password). Needs the PC on, you logged in and Docker Desktop running; secrets stay in the env file. Not yet run on a real PC.
