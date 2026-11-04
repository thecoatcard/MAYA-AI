# Documentation Policy

**Every meaningful code change includes the corresponding documentation update in the same change.** Documentation describes behavior that is implemented, not behavior that is planned.

## 1. What must be documented

The "Rule" column names the automated check in `governance/governance.config.json` (`changeRules`) that enforces the row once matching code exists. Rows without a rule are enforced in review.

| Change type | Required documentation | Rule |
| :--- | :--- | :--- |
| New module | Overview, responsibility, interfaces, dependencies (`templates/module-documentation/TEMPLATE.md`) | module README check |
| API change | API documentation with request and response examples (`templates/api-documentation/TEMPLATE.md`) | `api` |
| Database change | Schema documentation and migration notes | `database` |
| Architecture change | Architecture documentation and an ADR (`templates/adr/TEMPLATE.md`) | `architecture` |
| New integration | Setup, permissions, failure handling (`templates/integration-documentation/TEMPLATE.md`) | `integration` |
| Configuration change | Entry in `.env.example` and `docs/guides/CONFIGURATION.md` | `configuration`, env check |
| Security change | Security documentation and threat considerations | `security` |
| New runtime agent | Responsibility, tools, permissions, workflows (`templates/agent-documentation/TEMPLATE.md`) | `agent` |
| New background task | Lifecycle, retries, timeout, cancellation | `background-task` |
| Bug fix | Relevant docs if behavior or usage changes; changelog entry | changelog rule |
| Dependency update | Changelog or dependency note | `dependencies` |
| Deployment change | Deployment and operational documentation (`templates/operational-runbook/TEMPLATE.md`) | `deployment` |

## 2. General requirements

1. Every module has a README (or an entry under `docs/modules/`) once it exists.
2. Every public API is documented.
3. Every environment variable appears in `.env.example` and in the configuration reference, with a safe placeholder value. Never put real secrets in documentation.
4. Every architectural decision that materially affects the system has an ADR (`governance/CHANGE_MANAGEMENT.md`).
5. Update diagrams when the architecture they show changes.
6. Update setup instructions whenever the developer workflow changes. Keep the root `README.md` accurate.
7. Record meaningful changes in `CHANGELOG.md` (Keep a Changelog format, newest first, under "Unreleased" until a release).
8. Mark anything not yet built as "planned". Do not describe planned behavior in the present tense.
9. Prefer links over copies. A fact lives in one document.
10. Write for a new contributor, human or agent: what it is, why, how to use it, how it fails.

## 3. Exceptions

Documentation updates are not required for:

- Formatting, whitespace or typo-only changes
- Comment-only changes
- Test-only changes that do not change documented behavior
- Internal refactors that change no interface, configuration, behavior or operations
- Regenerated files (for example the per-tool wrappers produced by `npm run agents:sync`), when their source was documented

If a change would normally need documentation but genuinely needs none, say so explicitly:

- In the change report, under "Documentation Updated", with the reason.
- In the commit message, with a trailer: `Docs: not-required: <reason>`. The reason must be specific (at least a short sentence); `Docs: not-required: n/a` is rejected.

The same pattern applies to `ADR:` and `Changelog:` trailers (see `governance/GIT_WORKFLOW.md`).

## 4. Templates

Located in `templates/` (placeholders there are intentional and excluded from placeholder checks):

| Template | Use for |
| :--- | :--- |
| `templates/adr/TEMPLATE.md` | Architecture Decision Records |
| `templates/module-documentation/TEMPLATE.md` | Module documentation |
| `templates/api-documentation/TEMPLATE.md` | API documentation |
| `templates/integration-documentation/TEMPLATE.md` | Integration documentation |
| `templates/agent-documentation/TEMPLATE.md` | Runtime agent documentation |
| `templates/change-report/TEMPLATE.md` | Change reports |
| `templates/operational-runbook/TEMPLATE.md` | Operational runbooks |

## 5. Automated validation

`npm run docs:check` (also part of the pre-commit hook and CI) verifies:

- Required files from `requiredFiles` exist.
- Internal Markdown links resolve, including heading anchors.
- Repository paths written in inline code (for example `governance/SECURITY_POLICY.md`) point to files that exist. Paths in fenced code blocks, paths containing wildcards or placeholders, and directory references ending in `/` are not checked.
- Placeholder text (unfinished-work markers, filler text) does not appear outside code and `templates/`.
- Every environment variable read by application source is listed in `.env.example` and in `docs/guides/CONFIGURATION.md`, and nothing is documented that no longer exists.
- Every module directory under the configured roots has a README or a `docs/modules/` page.

Diff-based requirements (the table in section 1, ADRs, changelog) run through `scripts/validation/check-change-rules.mjs` in pre-push and CI, comparing the change range with its base branch.

Limits to be aware of: these checks prove documentation exists and links resolve, not that it is accurate. Accuracy is a review responsibility.
