"""Render MAYA's system e-mails to HTML files for a browser. Sends nothing, uses no network.

    uv run --directory backend python scripts/preview_emails.py <output-folder>

Writes ``<name>-light.html`` and ``<name>-dark.html`` per template. The dark file forces the dark
palette (a real client switches by itself through ``prefers-color-scheme``).
"""

import sys
from pathlib import Path

from app.auth.messages import code_message
from app.core.email_templates import render_html


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: preview_emails.py <output-folder>", file=sys.stderr)
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    samples = (
        ("email-verification", "email_verification", "042917"),
        ("password-reset", "password_reset", "518304"),
    )
    for name, purpose, code in samples:
        _, _, content = code_message(purpose, code, username="alice")  # type: ignore[arg-type]
        for scheme in ("light", "dark"):
            page = render_html(content, force_scheme=scheme)
            (out / f"{name}-{scheme}.html").write_text(page, encoding="utf-8")
    print(f"Wrote previews to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
