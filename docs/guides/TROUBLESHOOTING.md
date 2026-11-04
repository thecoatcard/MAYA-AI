# Troubleshooting

## A commit is rejected

| Message | Cause and fix |
| :--- | :--- |
| `Header must look like "<type>(<scope>): <summary>"` | Use a Conventional Commit, for example `docs: update architecture overview`. Allowed types are in `governance/GIT_WORKFLOW.md`. |
| `This commit changes guardrail files … needs a "Human-Approval" trailer` | You edited `AGENTS.md`, `governance/`, hooks, validators, workflows, `package.json` or similar. Add a final paragraph such as `Human-Approval: approved by the repository owner in review`. |
| `these files must not be committed` | A `.env`, key or credential file is staged. Run `git rm --cached <file>` and keep it out of the repository. |
| `potential secret(s) found` | The scanner never prints the value; open the reported file and line. If real, rotate the secret (`governance/SECURITY_POLICY.md` section 5). If it is an example, make it an obvious placeholder. |
| `broken link`, `refers to a file that does not exist` | Fix the path or link, or remove the reference. Paths in code blocks and ending in `/` are not checked. |
| `out of date (run "npm run agents:sync")` | You changed `AGENTS.md`, a workflow or a role. Run `npm run agents:sync` and commit the regenerated files. |

## The hooks do not run

1. Run `npm run setup`, then `git config --get core.hooksPath`; it should print `scripts/hooks`.
2. If another hooks path was already configured, setup refuses to replace it; re-run with `--force` if that is what you want.
3. Hooks are skipped by `git commit --no-verify`. That is possible by design; CI still runs the same checks.
4. On Windows the hooks run through Git for Windows' bundled `sh`. If a hook fails with `\r: command not found`, a file has CRLF line endings; `.gitattributes` forces LF, so re-checkout the file.

## A check says SKIPPED

`SKIPPED` means the check did not run:

- **Stack lint, type, test and build:** no `backend/pyproject.toml` or `apps/web/package.json` exists yet. Expected until application code is added.
- **YAML or workflow checks:** the pinned `yaml` package is not installed. Run `npm ci`. In CI a skipped YAML check is a failure.
- **Range checks:** no base branch could be found (no upstream, `origin/main` or `main`). Pass one explicitly: `node scripts/validation/validate.mjs --stage full --base <ref>`.

## A check fails only in CI

- Make sure your branch is rebased or merged with the base: range checks compare against `origin/<base>`.
- CI treats a skipped YAML or workflow check as a failure; run `npm ci` locally to reproduce.
- The change-range rules ask for documentation, an ADR or a changelog entry that the local hook cannot always see; run `npm run validate` before pushing.

## Windows notes

- `make` is not installed by default; use the `npm run` commands.
- Use Git Bash or PowerShell; the scripts normalise paths themselves.
- Python is not required for any governance command.

## Still stuck

Run the failing script directly (for example `node scripts/validation/check-docs.mjs`) to see its full output, then check `docs/guides/CONFIGURATION.md` for the relevant setting.
