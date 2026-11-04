---
applyTo: "scripts/**/*.mjs"
---

Rules for the repository automation scripts. The canonical instructions are in `AGENTS.md`.

- Zero dependencies beyond Node.js built-ins (the only dev dependency is the pinned `yaml` parser, used for syntax checks).
- Cross-platform: Windows, macOS and Linux. Use POSIX-style repository paths internally and pass process arguments as arrays, never through a shell with untrusted text.
- Check scripts exit 0 (pass), 1 (fail) or 3 (skipped, meaning the check did not run).
- Every script has tests next to it (`*.test.mjs`) run by `npm test`.
