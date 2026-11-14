# Real-Time Voice Subsystem

> **Status: Phase 12 implemented; protocol v2 ([ADR-0015](../adr/0015-voice-protocol-v2.md)) implemented in the backend and tested against a fake Live provider. Behaviour against the real Gemini Live service is not yet verified (owner live spike pending).** Bidirectional WebSocket audio bridge, WebAudio worklet capture, sub-150 ms barge-in protocol, transparent session resumption, and voice-to-task decoupling are implemented in `backend/app/voice/` and `apps/web/`.

## 1. Overview

The MAYA AI Voice Subsystem establishes real-time, low-latency, bidirectional conversational audio between the user and the Gemini Live API (`gemini-3.8-live`). It bridges audio over WebSockets, enforces voice-task decoupling, shields approvals from verbal authorization, and terminates audio playback within 150 ms upon interruption.

Key Architectural Decisions:
- **Decision D3 (Voice-Task Decoupling):** The voice model is strictly decoupled from task execution runtimes. The voice session holds `submit_task(instruction, deliverable)` and `get_task_status(task_id)`, plus (amended by the owner for memory, see ADR-0015 addendum) `recall_memory(query)` and `save_memory(text, kind?)` only when the user's memory setting is on. `forget_memory` is never exposed; voice can never approve (D5). Zero MCP tools exist in the voice context. Long-running tasks execute asynchronously on background workers; progress is bridged back into audio via Redis Pub/Sub.
- **Decision D5 (Voice Approval Shielding):** Spoken PINs, biometric approvals, or verbal authorizations are prohibited. Attempting to approve an action via voice returns `VoiceApprovalProhibitedError: "Approvals must be confirmed in the secure web interface."`
- **Decision D12 (NFR Latency & Barge-In):** Voice first-audio target latency is p95 <= 850 ms (target 450–600 ms). Barge-in interruption latency is <= 150 ms from speech detection to full playback buffer flush.

---

## 2. Audio Pipeline Architecture

```
User Microphone
      │ (16 kHz 16-bit mono PCM)
      ▼
AudioWorkletProcessor (apps/web/public/audio-processor.js)
      │ 100 ms buffers (1,600 samples / 3,200 bytes)
      ▼
Client Hook (apps/web/src/hooks/useLiveVoice.ts)
      │ WebSocket binary frames
      ▼
FastAPI Gateway (WS /api/v1/voice/live)
      │
      ▼
LiveVoiceSession (backend/app/voice/live_session.py)
      │
      ├──► Gemini Live API Client (bidirectional streaming)
      │
      ├──► VoiceToolRegistry (submit_task, get_task_status, recall_memory*, save_memory*; *memory on)
      │
      └──► VoiceEventBridge (listens to Redis task progress)
```

---

## 3. Sub-150 ms Barge-In Protocol (Decision D12)

When the user speaks while the model is outputting audio:
1. Client VAD detects speech onset and sends `{"type": "VAD_SPEECH_START"}` or `{"type": "CLIENT_INTERRUPT"}` to the backend. Alternatively, the Gemini Live server emits `interrupted: true`.
2. The `LiveVoiceSession.handle_interrupt()` method executes immediately:
   - Drains and flushes all pending audio chunks from the outgoing message queue.
   - Emits an `{"type": "AUDIO_INTERRUPT", "epoch": n}` control frame to the client and drops stale audio of the interrupted generation until the provider's turn boundary.
   - Resets internal speaking state: `is_speaking = False`, `is_interrupted = True`.
3. Client-side `useLiveVoice` hook receives `AUDIO_INTERRUPT`:
   - Instantly halts all active `AudioBufferSourceNode` playback sources.
   - Clears scheduled audio buffer queue.
4. Execution completes well within the 150 ms hard SLA.

---

## 4. Session Resumption and Transparent Reconnection

Gemini Live sessions are time limited and the provider sends `GoAway` before closing.
1. The bridge requests `session_resumption` and `context_window_compression` and stores the **real** handle the provider sends (`VoiceSessionManager`, Redis key `voice:resumption:{workspace_id}:{session_id}`, 900 s; process memory without Redis). No handle is ever fabricated.
2. On `GoAway` the client gets `GO_AWAY`; the bridge reconnects with the handle and the client gets `SESSION_RENEWED`.
3. Without a handle the session cannot continue transparently: the client gets a fatal `ERROR` and close 4503 and reconnects with the same `session_id`, which re-seeds the model with the last 20 turns from the database.
4. Known limitation: the handle's real validity window and `GoAway` timing are unverified until the live spike.

## 4a. One conversation

Voice attaches to a chat session (`channel='web'`) and reports its id in `READY`. Completed turns are saved one by one (`messages.modality` `audio` or `text`), typed `USER_TEXT` is part of the same turn stream, and task events of the session are forwarded as `TASK_EVENT`. When Live is unavailable the server sends `LIVE_UNAVAILABLE` and closes; there is no simulator.

## 5. Security & Governance Safeguards

- **Tenant Isolation:** WebSocket authentication verifies the caller's JWT access token, resolves the `Principal`, and enforces workspace row-level security.
- **Zero MCP Tools in Voice Context:** Only `submit_task`, `get_task_status` and (memory on) `recall_memory` and `save_memory` are declared to the model. Attempts to register or execute MCP tools raise `MCPToolRegistrationBlockedError`.
- **Approval Shielding:** Voice can announce pending approvals, recite summaries, and accept cancellations or denials. Any spoken intent to approve is rejected with explicit instructions to confirm in the authenticated web UI.
