# MCP client adapter (ADR-0028, Proposed)

Lets MAYA use third-party MCP servers (built-in definitions: `playwright`, `github`) in chat and in
live voice, behind MAYA's own permission gate. **Off by default.**

**Security first.** The servers run on the HOST as child processes with your operating-system
rights; the existing sandbox does not isolate them. Keep a server disabled until you have reviewed
the exact package and version.

## Modules

| File              | Role                                                                          |
| :---------------- | :---------------------------------------------------------------------------- |
| `definitions.py`  | Server definitions, exact-version pin check, servers file, built-ins          |
| `policy.py`       | Tiers (READ list, else approval) and the private-address refusal              |
| `schema.py`       | JSON Schema to the Gemini subset, tool names `mcp__<server>__<tool>`          |
| `runner.py`       | One child process behind the `mcp` SDK (the only module that imports it)      |
| `service.py`      | Lazy start, timeout, budget, output cap, bounded restart, per-user processes  |
| `chat.py`         | Tool selection per message, READ run, approval proposal, approved execution   |
| `voice.py`        | The same, for live voice                                                      |
| `gateway_spec.py` | Registers the approved-execution tool `mcp_client_call` with the MCP gateway  |

## Enable one server safely

1. Review the package (`@playwright/mcp` or the GitHub MCP server binary) and choose an exact
   release.
2. Copy `servers.example.json` outside the repository, set `"enabled": true` and the exact
   `"version"` for ONE server only.
3. In your private env file set `MAYA_MCP_CLIENT_ENABLED=true` and `MAYA_MCP_SERVERS_FILE` to the
   file's path (see `docs/guides/CONFIGURATION.md`). Restart the API.
4. Check `GET /api/v1/mcp-client/status` (signed in): the server shows `enabled: true`.
5. For `github`, connect GitHub in Settings > Connections first (its token is injected only into
   that user's server process).
6. Try a read request first. Every write or interactive action shows an approval card with the
   exact tool and arguments.

## Tier map

- `playwright` READ: `browser_snapshot`, `browser_take_screenshot`, `browser_console_messages`,
  `browser_network_requests`. `browser_navigate` is READ only for a host the user wrote in the
  message and a public address; otherwise approval. Everything else (click, type, fill_form,
  evaluate, file_upload, run_code, tabs, ...) and any unknown tool: APPROVAL_REQUIRED.
- `github` READ: get_me, get_file_contents, get_issue, get_issue_comments, issue_read, list_issues,
  search_issues, get_pull_request (+ _files, _diff, _comments, _reviews, _status),
  pull_request_read, list_pull_requests, search_pull_requests, list_commits, get_commit,
  list_branches, list_tags, get_tag, search_code, search_repositories, search_users,
  list_workflows, list_workflow_runs, get_workflow_run. Everything else (issues, PRs, comments,
  branches, files, merges, forks) and any unknown tool: APPROVAL_REQUIRED.

## Not covered

DNS rebinding after MAYA's address check and browser-side redirects. Tool names above come from the
servers' public documentation and were not checked against a running server.
