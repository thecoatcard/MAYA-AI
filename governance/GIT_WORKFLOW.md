# Git Workflow

## 1. Branches

- `main` is protected: no direct pushes, required checks, reviews required (see `docs/operations/BRANCH_PROTECTION.md`; a repository administrator configures this). The server rejecting direct pushes to `main` has been observed (2026-11-05); which of the other rules are active has not been verified, and that document records the status.
- Work on short-lived branches named `<type>/<short-description>`, for example `feat/mcp-gateway-pool` or `fix/task-cancellation`.
- Agents commit only when asked, never to `main`, and never force-push.

## 2. Commit messages (Conventional Commits)

```text
<type>(<optional scope>): <summary in the imperative, up to 100 characters>

<optional body: why, not just what>

<optional trailers>
```

| Type | Use for |
| :--- | :--- |
| `feat` | A new capability |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `test` | Adding or correcting tests |
| `refactor` | Behavior-preserving restructuring |
| `security` | Security hardening or a vulnerability fix |
| `perf` | Performance improvement |
| `build` | Build system or dependency changes |
| `ci` | CI configuration |
| `style` | Formatting only |
| `chore` | Maintenance with no production effect |
| `revert` | Reverting an earlier commit |

Use `!` after the type or a `BREAKING CHANGE:` footer for breaking changes. Merge, revert, `fixup!` and `squash!` commits are accepted as generated.

## 3. Trailers

Trailers are the last lines of the message, in `Key: value` form. They make decisions auditable and satisfy the diff-based checks.

| Trailer | When | Example |
| :--- | :--- | :--- |
| `Human-Approval:` | The commit touches a path in `sensitive.approvalRequiredPaths` | `Human-Approval: approved in session by the repository owner` |
| `Docs:` | Waives a documentation rule | `Docs: not-required: internal rename, no interface change` |
| `ADR:` | Waives the architecture rule | `ADR: not-required: bug fix inside an existing design` |
| `Changelog:` | Waives the changelog rule | `Changelog: not-required: no user-visible effect` |

A waiver must state a specific reason. The `Human-Approval:` trailer is an audit trail, not a cryptographic control: anyone can type it. Its value is that an approval claim becomes visible and reviewable. Real enforcement is branch protection, plus CODEOWNERS once a file is committed (see `docs/operations/BRANCH_PROTECTION.md` for what is verified).

## 4. Hooks (installed by `npm run setup`)

Git is configured with `core.hooksPath = scripts/hooks`, so the hooks live in the repository and work for every contributor and agent without per-tool setup.

| Hook | Runs | Aim |
| :--- | :--- | :--- |
| `pre-commit` | Sensitive-file block, secret scan of staged content, whitespace and conflict markers, config syntax, script syntax, agent-file drift check, documentation check, stack lint and type checks (if projects exist) | Fast; seconds |
| `commit-msg` | Conventional Commits format; `Human-Approval:` trailer for sensitive paths | Instant |
| `pre-push` | Everything above for the whole repository, unit tests, stack tests and build, plus change-range rules against the base branch (documentation, ADR, changelog, approvals) | Slower; minutes at most |

If a hook is too slow or noisy, fix the hook. Do not tell contributors to skip it.

**Hooks can be bypassed** (`--no-verify`, a missing `npm run setup`). They are a convenience that catches problems early. CI runs the same validators on every pull request, and branch protection is meant to make them required (only the pull-request rule has been observed to be enforced; until the rest is verified, a failing CI result must be acted on by the merger).

## 5. Pull requests

- Use the pull request template (`.github/PULL_REQUEST_TEMPLATE.md`); it mirrors the change report.
- One logical change per pull request; keep it reviewable.
- CI must pass: governance validation, secret scan, dependency review.
- A human reviews and merges. Squash-merge with a Conventional Commit title.

## 6. Things not to do

- Do not rewrite shared history, amend pushed commits, or force-push.
- Do not commit generated files by hand: run `npm run agents:sync`.
- Do not commit `.env` files, keys, dumps or build output.
- Do not mix unrelated changes in one commit.
