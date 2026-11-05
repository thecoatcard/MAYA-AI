# MAYA web foundation

A Next.js 15 App Router scaffold using React 19, strict TypeScript, Tailwind CSS
and Lucide icons. The overview is a static server-rendered page with real section
navigation. Product cards are explicitly labelled planned; no chat or sign-in
behavior is simulated.

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
page contacts nothing. The only variable read is `NEXT_PUBLIC_API_BASE_URL` (see
"Chat" below).

Look, feel and motion follow the [UI/UX standards](../../docs/architecture/UI_UX_STANDARDS.md); local agent rules
for this folder are in [AGENTS.md](AGENTS.md). The current page is a plain foundation scaffold, not the product design.

Vitest and Testing Library cover the rendered page, roadmap states and navigation.
The [test strategy](../../docs/testing/FOUNDATION.md) describes the verification boundary.

## Chat (`/chat`)

The text-chat canvas for the Phase 04 API ([contract](../../docs/api/CHAT.md),
[ADR-0008](../../docs/adr/0008-chat-api-contract.md)). It is statically prerendered
and never calls the API at build time or on load.

**Status.** There is no sign-in screen in the product yet, so `/chat` shows
"Sign-in is not built yet" unless an access token has been put into memory with
`setAccessToken` from `src/lib/auth-session.ts`. Nothing in the shipped app calls it.
The token is held in memory only; it is never written to localStorage, sessionStorage
or cookies. The chat has not been exercised in a real browser against a running API.

**Configuration.** `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`) is the
API origin. Browser-to-API connectivity (CORS on the API, or a same-origin proxy) is
not decided and not built; the client only needs a reachable base URL. The access token
travels in a header on every request, so the client refuses a plain `http://` base URL
unless the host is loopback (`127.0.0.1`, `localhost`, `::1`): it throws a configuration
error before anything is sent, and the chat shows "The chat server address is not set up
safely". An `https://` URL and a same-origin path such as `/api-proxy` are accepted.

**Where things are**

| Path                         | What it does                                                                                                                                                                                                                                                                                       |
| :--------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/sse.ts`             | Incremental SSE parser over a byte stream (split frames, split multi-byte characters, CRLF/LF/CR, comments)                                                                                                                                                                                        |
| `src/lib/api-client.ts`      | `streamChatMessage`, `listSessions`, `listMessages`, typed `ApiError`, `NetworkError` and `ApiConfigurationError` (unsafe base URL), event decoding (unknown events are ignored)                                                                                                                   |
| `src/lib/auth-session.ts`    | In-memory access token holder, plus `getAccessTokenVersion()`, a counter that rises on every change of token (the chat compares it, never the token)                                                                                                                                               |
| `src/lib/markdown.ts`        | Dependency-free safe Markdown subset (no HTML strings)                                                                                                                                                                                                                                             |
| `src/hooks/useChatStream.ts` | Conversation state machine: one Idempotency-Key per message reused on every same-key retry, replay replaces partial text, bounded automatic retry (3 attempts, backoff with jitter) for network errors and 409, `Retry-After` for 429, `stop()`, and text applied at most once per animation frame |
| `src/components/chat/`       | `ChatWorkspace` (the page), `MessageThread`, `MarkdownRenderer`, `CodeBlock` (plus `highlight.ts`), `ChatInput`, `ChatNotice`, `ThemeToggle`, and `chat.css` (design tokens and rules, light and dark)                                                                                             |

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
