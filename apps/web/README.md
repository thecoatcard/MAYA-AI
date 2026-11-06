# MAYA web foundation

A Next.js 15 App Router application using React 19, strict TypeScript, Tailwind CSS
and Lucide icons. The Direction A — Quiet Instrument landing page describes only
available and planned capabilities. Account pages and authenticated text chat use
the existing Phase 02 and Phase 04 APIs; nothing is simulated.

From the repository root:

```powershell
npm --prefix apps\web ci
npm --prefix apps\web run dev
npm --prefix apps\web run lint
npm --prefix apps\web run typecheck
npm --prefix apps\web test
npm --prefix apps\web run build
```

Development and production-start scripts bind to loopback. The pages use system
fonts and do not contact a model provider or an external font service. The overview
page contacts nothing. Chat and sign-in use a same-origin API proxy configured
with the server-only `MAYA_API_PROXY_TARGET` variable (see "Chat" below).

Look, feel and motion follow the [UI/UX standards](../../docs/architecture/UI_UX_STANDARDS.md);
Direction A was selected on 2026-11-06. Local agent rules for this folder are in
[AGENTS.md](AGENTS.md).

Vitest and Testing Library cover the rendered page, roadmap states and navigation.
The [test strategy](../../docs/testing/FOUNDATION.md) describes the verification boundary.

## Chat (`/chat`)

The text-chat canvas for the Phase 04 API ([contract](../../docs/api/CHAT.md),
[ADR-0008](../../docs/adr/0008-chat-api-contract.md)). Static rendering does not
call the API; after the page loads, it checks the refresh cookie and loads history
only after authentication succeeds.

**Pages and authentication.** `/login`, `/register`, `/verify`, `/forgot-password`,
and `/reset-password` use the Phase 02 account API and generic responses that do not
reveal whether an address exists. `/chat` checks the same-origin `/refresh` endpoint
on load and shows a skeleton while checking. The browser sends
`X-Maya-Refresh-Transport: cookie` with same-origin credentials on `/token`,
`/refresh`, and `/logout`. Refresh tokens are `HttpOnly` cookies and are never
available to JavaScript; only the access token stays in memory. It refreshes before
expiry and shares concurrent refreshes. Sign-out clears local state even if the
server cannot be reached.

The chat sidebar lists sessions grouped by Today and Earlier, paginates history,
loads stored messages oldest-first, and continues an opened session. Session create,
rename and delete endpoints are not part of the MVP. The mobile history sidebar is
a keyboard-managed drawer; theme preference is system-based with a manual switch.

**Configuration.** The browser sends API requests to same-origin `/api/v1` paths.
Next.js rewrites those paths to the origin in the server-only
`MAYA_API_PROXY_TARGET` variable (default `http://127.0.0.1:8000`). Non-loopback
targets must use HTTPS; loopback HTTP is supported for local deployments.
Credentials, paths, query strings and fragments are rejected. The variable
is not `NEXT_PUBLIC_*` and is not included in the browser bundle. This same-origin
proxy avoids CORS preflight for the `Authorization` and `Idempotency-Key` headers
and does not require exposing `Retry-After`.

Both proxy settings are read at **build time**: Next.js writes rewrites into the
build output, so changing them needs `npm run build` again. The proxy timeout is
`MAYA_CHAT_REPLY_DEADLINE_SECONDS` (default 120, the same variable the API reads)
plus 30 seconds, so the API's reply deadline, not the proxy's 30-second default,
ends a silent reply.

**Where things are**

| Path                          | What it does                                                                                                                                                                                                                                                                                       |
| :---------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/sse.ts`              | Incremental SSE parser over a byte stream (split frames, split multi-byte characters, CRLF/LF/CR, comments)                                                                                                                                                                                        |
| `src/lib/api-client.ts`       | `streamChatMessage`, `listSessions`, `listMessages`, typed `ApiError`, `NetworkError`, same-origin paths, event decoding (unknown events are ignored)                                                                                                                                              |
| `src/lib/auth-client.ts`      | Same-origin login, logout and account requests; opts into HttpOnly-cookie refresh transport                                                                                                                                                                                                        |
| `src/lib/auth-session.ts`     | In-memory access-token holder and single-flight cookie refresh; refresh tokens are never available in JavaScript                                                                                                                                                                                   |
| `src/lib/api-proxy-target.ts` | Validates the server-side upstream origin and refuses non-loopback HTTP or URL components other than an origin                                                                                                                                                                                     |
| `src/lib/markdown.ts`         | Dependency-free safe Markdown subset (no HTML strings)                                                                                                                                                                                                                                             |
| `src/hooks/useChatStream.ts`  | Conversation state machine: one Idempotency-Key per message reused on every same-key retry, replay replaces partial text, bounded automatic retry (3 attempts, backoff with jitter) for network errors and 409, `Retry-After` for 429, `stop()`, and text applied at most once per animation frame |
| `src/components/auth/`        | Sign-in and reusable account forms with safe error messages, accessible inputs, and the shared theme                                                                                                                                                                                               |
| `src/components/chat/`        | `ChatWorkspace`, paginated session history, `MessageThread`, safe Markdown, composer, notice, theme toggle, and shared design tokens/rules                                                                                                                                                         |

The chat uses the existing `/chat` design tokens and the owner-approved `system-ui`
typeface exception. No font service or new dependency is used. A Content-Security-Policy
is deferred until the hosting/runtime target is confirmed.

The approved chat defaults are 8000 message characters, 20 starts per user per minute,
32000 context tokens, two concurrent streams per user, a 120-second reply deadline and
2048 output tokens. Context token counts are local conservative estimates and have not
been compared with Gemini's tokenizer. The owner-run Gemini smoke check and browser
acceptance (sign in, send, stream, then reconnect without a second model call) remain
pending; do not infer acceptance from the jsdom tests. Playwright as a dev dependency
was approved for browser acceptance but was not added for this implementation.
History-query indexes are deferred pending query-volume evidence and a rollback plan.

**Rendering safety.** Assistant text is rendered as React elements from a parsed tree,
never through `dangerouslySetInnerHTML` or `innerHTML`. Raw HTML is shown as text; links
are produced only for `http`, `https` and `mailto` without credentials (opened with
`rel="noopener noreferrer"`); `javascript:`, `data:`, `vbscript:` and relative URLs stay
plain text; Markdown images are never loaded (the alt text is shown). Only the five basic
entities (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`) are decoded inside a link URL, so
`&#106;avascript:` and `javascript&colon;` never become a scheme. Tables, setext headings
and reference links are not in the supported subset and appear as plain text.

- **Where a link goes.** Every link shows its real destination after the link text, as
  `label (host)` (the ASCII form of the host, with a non-default port; the address for
  `mailto`). Screen readers hear "goes to host" and "opens in a new tab". The host is left
  out only when the link text is itself that address on the same host, so
  `[https://mybank.example/login](https://evil.example/phish)` shows `(evil.example)`.
  Direction-control characters are stripped from link text so they cannot reorder the host.
- **Code.** Direction-control characters (U+202A to U+202E, U+2066 to U+2069) are shown as
  visible `<U+202E>` markers in code blocks and inline code instead of being obeyed. The Copy
  button copies the marked text.
- **Bounded work.** No regular expression runs over unbounded input. Link and image
  attempts that fail share a work budget per parse, parentheses at the end of a bare URL are
  counted once, and input over 200,000 characters is shown without formatting. Streamed text
  is applied at most once per animation frame, so a long reply is parsed once per frame and
  not once per piece of text. A static test scans every shipped source file for
  `innerHTML`, `outerHTML`, `dangerouslySetInnerHTML`, `insertAdjacentHTML`, `document.write`,
  `eval` and `new Function`, and for invisible characters.

**Retry and Ask again.** The server stores every finished outcome under the
Idempotency-Key for 24 hours, failures included, and answers a repeat of the same key and
body with the stored result without calling the model again (a replayed reply arrives as
one complete `delta` that replaces any partial text). So the client offers two different
actions:

| Action        | What it does                                                           | Offered when the server's outcome is unknown to the client                                                                                                                                                                                     |
| :------------ | :--------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Retry**     | Sends the same message again with the **same** key (never a duplicate) | Network error; a stream that ended without `done` or `error`; 409 `request_in_progress`; 429 (enabled after `Retry-After`); 503 `chat_unavailable`; any other 5xx without a documented code (for example a proxy 502 or 504 with an HTML body) |
| **Ask again** | Sends the same text as a **new** message with a **new** key            | After an `error` event, and whenever the last message is an interrupted reply                                                                                                                                                                  |

After an `error` event (any code, whatever its `retryable` flag says; the flag is ignored)
only Ask again is offered, because the same key would replay the same error. Ask again also
appears as a small button inside the "Reply interrupted" note when the last message is an
interrupted reply and no error notice is showing (for example after an automatic retry ended
in a replayed `done` with `finish_reason: "interrupted"`, or after the notice was dismissed).
Automatic retry (3 attempts) still covers only a network failure and 409.

**Messages that may have been received.** A user message is labelled "Not sent" only when the
server answered without accepting it. After a network error, a 409 or an unknown 5xx the
message is labelled "Not confirmed. The server may have received this message.", and Retry
leads. Stopping a request before the server answered gives the same label. Dismissing the
notice for such a message keeps the message in the thread and does not put its text back into
the composer, so a second copy is never invited silently. (A "Not sent" message is removed on
dismiss and its text returns to the composer.)

**Accessibility notes.** In an error notice only the static message is a live region
(`role="alert"`); the buttons and the visual countdown sit outside it. The Retry button's
name stays "Retry" while a `Retry-After` wait counts down (the countdown is `aria-hidden`
text beside it, with a static description), and the countdown is computed from a deadline,
so a throttled background tab does not keep Retry disabled for longer than the server asked.
After Retry, Ask again or Dismiss, focus moves to the composer (as it does for Stop and New
chat). The focused composer gets the same 2 px outline as every other control.

**Sign-in identity.** The chat resets (request aborted, messages and unsent draft cleared)
whenever the access token changes, including from one token straight to another, not only
when it disappears. Because the token is the only identity today, a future silent refresh of
the same user's token would also reset the chat; sign-in work needs a separate user or
session identity for that.

**Not built yet.** A session list or history view (the client has `listSessions` and
`listMessages`, no screen uses them), a sign-in screen, voice, and task events. The 8000
character input limit is provisional and mirrors the server setting. No typeface has been
approved, so the page uses a system font stack behind one token. Playwright is not
installed, so there are no browser tests; the component tests run in jsdom, which does no
layout or painting: contrast, focus outlines, touch-target sizes and hover states are held
by static checks of `chat.css`, not by looking at a rendered page. An idle or stall
watchdog for a stream that goes silent (it needs a server heartbeat decision), a
Content-Security-Policy (deployment configuration), a chosen typeface and spoken
announcement of a finished reply are open owner decisions and are not built.
