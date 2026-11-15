# MCP Governance & Permission Engine

Tool permission classification, human approval gating, cryptographic token verification, and input sanitization (Phase 07, Decisions D4 & D5).

## Modules

- `permissions.py`: Four-tier permission classification (`READ`, `WRITE`, `APPROVAL_REQUIRED`, `RESTRICTED`) with deny-by-default enforcement. Unregistered tools are rejected with `PermissionDeniedError`. MCP server hints can only elevate permission tiers, never lower them.
- `approvals.py`: Deterministic canonical JSON serialization and SHA-256 payload digest engine (`compute_payload_hash`) binding execution arguments, tool name, workspace ID, user ID, and idempotency key.
- `tokens.py`: Cryptographically signed single-use HS256 JWT minting (`mint_approval_token`, capped at 5 minutes / 300s) and atomic PostgreSQL CAS consumption (`consume_approval_atomically`) preventing replay and TOCTOU race conditions.
- `sanitizer.py`: Multi-layered defense-in-depth sanitization for untrusted external tool content. Strips null bytes, ANSI escape sequences, BiDi overrides, normalizes Unicode NFKC, runs heuristic prompt-injection scanners, and wraps outputs in structural boundary markers (`<<<UNTRUSTED_EXTERNAL_INPUT>>>`).
- `github_repo_tools.py`: the shared `RepoToolkit` behind the six read-only GitHub repository tools
  (gateway, chat and voice all use it): repo URL or `owner/name` normalisation (github.com only,
  requests only to api.github.com), size caps and truncation notices, binary refusal, secret
  redaction (memory scrubber plus `.env` and key-assignment patterns), untrusted-data wrapping
  (`<<<REPO_DATA>>>`), a per-turn call and read budget, and the code-review instruction.
  Public repositories work without a token (unauthenticated, 60 requests per hour); private ones
  use the caller's own connection. Connector methods live in `connectors/github.py`.
- `gateway.py`: `McpGateway` resolves a tool to its server (built-in `maya-tools` first, then the
  workspace's pinned `mcp_servers` rows), starts it lazily under `ProcessSupervisor`, and invokes
  it through `MCPClientManager` (deny-by-default gate, rate limit, output sanitiser). Built-in
  tools: `web_search` (Gemini Google Search grounding through the Model Gateway; the result ends
  with a numbered `Sources:` list), `github_list_repositories` (read-only), the six read-only
  repository tools `github_get_repo`, `github_list_tree`, `github_read_file`,
  `github_list_commits`, `github_list_pull_requests`, `github_list_issues` (see
  `github_repo_tools.py`; all READ tier) and `github_create_pull_request` (approval-gated). Credentials come from settings and live inside the server closure. A missing
  credential or dead server raises `ToolUnavailableError` (the subtask fails, never fakes
  success). Google grounding retries one empty response with a concise, source-focused prompt;
  if that also returns no content, the tool fails without inventing findings or citations.
  Limits: DB-registered servers get no per-server environment yet (encrypted env
  storage has no key management), and unpinned launchers (`@latest`, `npx -y`) are refused.

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).


## Notion tools

Served by the built-in `maya-tools` server over Notion's REST API (`connectors/notion.py`, the
`Notion-Version` of `app/connections/providers.py`); the hosted official Notion MCP server is not
used because it needs an interactive OAuth sign-in.

| Tier | Tools |
| :-- | :-- |
| READ | `notion_search`, `notion_list_tasks`, `notion_list_databases`, `notion_get_database`, `notion_query_database`, `notion_find_page`, `notion_get_page` (`notion_tools.py`) |
| APPROVAL_REQUIRED | `notion_create_page`, `notion_append_blocks`, `notion_add_row`, `notion_update_row` (`notion_write.py`); `notion_create_page_from_job` (`notion_book.py`); `notion_update_page`, `notion_archive_page`, `notion_update_block`, `notion_delete_block`, `notion_add_comment`, `notion_replace_section` (`notion_edit.py`) |

Writes are proposed from a chat tool call (reads only), shown as the exact request list, sent once
after the user approves that payload and read back for verification. Deletes are Notion's trash.
Note content may be given as Markdown (`notion_markdown.py`).

## Large Notion pages from a job (`notion_book.py`, ADR-0031)

`notion_create_page_from_job` (APPROVAL_REQUIRED, never exposed to the chat model: only the learning-book plan names it) creates ONE Notion page from the Markdown a finished job stored as an `.md` artifact.

- **Prepare** (worker, just before the user is asked): reads the task's Markdown artifact, converts it with `notion_markdown.py` and returns small `tool_args` (title, parent page, artifact id and file name, SHA-256 of the Markdown, block, word, chapter, code, table and equation counts, emoji icon, optional cover) and a readable summary. The approval's payload hash covers them, so it binds the exact content. The card shows the outline and counts, the first 40 lines and the full text on demand (it fetches the artifact), and the exact payload.
- **Execute** (gateway, only after the worker verified and claimed the approval): re-reads the file, refuses when its hash or block count differ, creates the page with the first batch and appends the rest, at most 100 top-level blocks, 900 elements and 400 KB per request. Text over 2,000 UTF-16 units and tables over 100 rows are split by the converter, never cut; list nesting stays at two levels. Only a 429 is waited out (Retry-After, at most 30 s, three tries) and resent; any other failure may have been applied and is not repeated.
- **Converter additions**: a `[toc]` line is Notion's table-of-contents block; code fences are normalised to Notion's language list (`js`, `py`, `shell`, `c++` ... unknown becomes plain text). The icon must be one emoji (default a book); a cover must be an https URL on `COVER_HOSTS`, otherwise it is skipped and the card says so.
- **Read-back**: the page's children are read back and compared with what was sent (count, headings, types, text); the result says VERIFIED, lists differences, or says NOT VERIFIED when the check could not run. A failure part-way reports exactly how many blocks were written and the arguments to continue (`resume_page_id`, `resume_from_block`, appended after a new approval); it never claims success.
- **Auto-approve**: the tool is in `ALWAYS_MANUAL_TOOLS`, and any write above 300 blocks is manual whatever the owner's scopes say.
- **Permissions**: one additive entry in `permissions.py` (`notion_create_page_from_job`: APPROVAL_REQUIRED).

## Notion auto-approve (opt-in, default off)

`notion_auto_approve.py`: an owner setting (`GET/PUT /api/v1/settings/notion-auto-approve`, stored in `user_settings`) lets `notion_create_page`, `notion_add_row`, `notion_append_blocks` and `notion_update_row` be approved without a click. Only the authenticated Settings API writes it, and turning it on needs `confirm: true` from the Settings dialog. The worker records the approval at proposal time with the same repository call and signed token a human click produces, so the gate still verifies the payload hash, claims the approval and runs the read-back check. It is stored with `decision_channel = 'api'` (the CHECK allows only `web` and `api`; a new value needs a migration) plus an `approval.auto_approved` audit row (marker `auto_setting`). Archive, delete, replace-section, text edits and comments are hard-coded as never auto-approvable. Caps: 30 per hour, 200 per day, then manual with a note on the card.
