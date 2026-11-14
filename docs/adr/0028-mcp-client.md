# ADR-0028: MCP client adapter (third-party MCP servers behind MAYA's gate)

- Status: Proposed
- Date: 2026-11-12

## Context

The owner asked that MAYA can use third-party MCP servers, first Playwright MCP (a browser) and the
official GitHub MCP server. A third-party server is code MAYA did not write, started on the
owner's machine, whose output is text written by strangers (web pages, issues).

As ADR-0027 found, the existing `SandboxRunner` does not isolate anything. **MCP servers started by
this adapter run on the HOST** as child processes with the owner's operating-system rights. A
browser server also reaches whatever the host can reach (the owner's network).

## Decision

1. New package `backend/app/mcp_client/` using the official `mcp` Python SDK (pinned `mcp==2.3.0`),
   stdio transport only. One child process per server per app; for a server that holds a user's
   credential (GitHub) one per user, so a token is never shared between users.
2. Off by default: `MAYA_MCP_CLIENT_ENABLED=false` is the kill switch. While off nothing is built,
   the SDK is not imported, no process starts and no MCP tool is offered to the model.
3. Every server is disabled until the owner enables it in a servers file (`MAYA_MCP_SERVERS_FILE`)
   AND pins an exact version. `@latest`, dist-tags, ranges and a missing version are refused.
   `playwright` and `github` ship as definitions only (disabled, no version).
4. The child gets only the environment variables named in `env_allow` (never `MAYA_*`) plus the
   SDK's short default list (PATH and similar). The GitHub token comes from the signed-in user's
   own GitHub connection and is injected into the child environment only; it is never logged,
   returned or stored.
5. Tiers use `app/mcp/permissions.py`: a tool is READ only when the definition lists it as
   read-only; a destructive hint can only raise it; every other tool, known or not, is
   APPROVAL_REQUIRED. One additive entry, `mcp_client_call` (APPROVAL_REQUIRED), is the only place
   a non-read call executes.
6. The model never holds a session. A READ call runs in chat or voice. Any other call only proposes
   one approval-gated task step (`mcp_client_call`) showing the exact server, tool and arguments on
   the existing approval card. The real MCP call happens only in the approved-execution path. Typed
   or spoken text never approves.
7. Tool output is untrusted: capped, wrapped in the `UNTRUSTED_EXTERNAL_INPUT` envelope with the
   rule to never follow it. Tool descriptions are untrusted too (cleaned, capped, replaced when
   they look like instructions). The tool list is fingerprinted: a server whose tools change after
   a restart is blocked until MAYA restarts.
8. Browser servers: any URL argument is inspected and refused unless it is a public http(s)
   address (localhost, private, link-local, CGNAT, metadata, internal names, odd numeric forms,
   non-http schemes, names resolving to private addresses). Opening a page is automatic only for a
   host the user wrote in the message. The check runs in chat, in voice and again when an approved
   call executes.
9. Limits: per-call timeout, per-turn call budget, output size cap, at most ~12 tools declared per
   turn chosen by relevance, bounded restart after a crash (then a one-minute pause), at most 4
   live processes.

## Consequences

- Enabling a server lets third-party code run on the host. The owner must review the pinned
  package and version first. There is no container isolation for this adapter.
- DNS rebinding between MAYA's check and the browser's own lookup, and redirects followed inside the
  browser, are not covered by the address check. Consider the browser server's own origin
  restrictions as extra hardening.
- Both chat and live voice use the same tools, tiers and approval path.

## Status of the implementation

Implemented and tested offline against an in-repo fake MCP server (real child process): discovery,
call, timeout, crash and restart, output cap, schema conversion, version pinning, environment
allowlist, approval gate, address refusal, untrusted wrapping, kill switch. NOT verified: any real
Playwright or GitHub MCP server (none was run), their real tool names and flags, and a real
approval click in the UI. The owner must review this ADR and try one server by hand before relying
on it.
