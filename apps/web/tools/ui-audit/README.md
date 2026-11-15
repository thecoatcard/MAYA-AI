# UI audit harness

Drives the static desktop export in a real Edge window (Playwright for Python, `channel="msedge"`) with every
`/api/v1/*` request mocked by `fixtures.py` (empty, huge, long-string, error, expired-session and slow states).
It measures horizontal page overflow, clipped text, elements wider than their parent, overlapping siblings,
elements outside the viewport and focus visibility, per page, width, theme and state, and writes screenshots plus
a machine-readable `results.json`.

```
npm --prefix apps/desktop run build:web          # writes apps/web/.next-desktop
backend/.venv/Scripts/python apps/web/tools/ui-audit/harness.py --help
```

Playwright is already in `backend/.venv`; nothing needs `playwright install` (Edge is used). Output goes to the
directory given by `--out`; do not commit screenshots. A full `--plan standard --workers 4 --keyboard` run takes
about 35 minutes. The detectors `wider` and `clippedY` also fire on intentional scroll regions, so compare counts
against a baseline instead of expecting zero. Details: `docs/architecture/UI_PAGE_STATE_MATRIX.md` section 7.
