# Request and response schemas

Pydantic models that define what the API accepts and sends. Each model rejects unknown fields, and validation
errors name fields without repeating submitted values.

`chat.py` (Phase 04) holds the chat request, the session and message read models, the page wrapper, the payloads of
the Server-Sent Events (`message.accepted`, `delta`, `done`, `error`) and `encode_sse`, the only place that writes an
event frame. `encode_sse` accepts only plain event names (letters, digits, dot, dash, underscore), one numeric id and exactly one JSON `data` line, and
escapes the Unicode line separators that some proxies treat as line ends. The contract is
[docs/api/CHAT.md](../../../docs/api/CHAT.md).
