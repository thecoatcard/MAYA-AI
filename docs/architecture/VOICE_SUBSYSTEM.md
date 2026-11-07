# Real-Time Voice Subsystem

> **Status: Phase 12 Implemented & Verified locally.** Bidirectional WebSocket audio bridge, WebAudio worklet capture, sub-150 ms barge-in protocol, transparent session resumption, and voice-to-task decoupling are implemented in `backend/app/voice/` and `apps/web/`.

## 1. Overview

The MAYA AI Voice Subsystem establishes real-time, low-latency, bidirectional conversational audio between the user and the Gemini Live API (`gemini-3.8-live`). It bridges audio over WebSockets, enforces voice-task decoupling, shields approvals from verbal authorization, and terminates audio playback within 150 ms upon interruption.

Key Architectural Decisions:
- **Decision D3 (Voice-Task Decoupling):** The voice model is strictly decoupled from task execution runtimes. The voice session holds exactly two tools: `submit_task(intent, params)` and `get_task_status(task_id)`. Zero MCP tools exist in the voice context. Long-running tasks execute asynchronously on background workers; progress is bridged back into audio via Redis Pub/Sub.
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
      ├──► VoiceToolRegistry (submit_task, get_task_status)
      │
      └──► VoiceEventBridge (listens to Redis task progress)
```

---

## 3. Sub-150 ms Barge-In Protocol (Decision D12)

When the user speaks while the model is outputting audio:
1. Client VAD detects speech onset and sends `{"type": "VAD_SPEECH_START"}` or `{"type": "CLIENT_INTERRUPT"}` to the backend. Alternatively, the Gemini Live server emits `interrupted: true`.
2. The `LiveVoiceSession.handle_interrupt()` method executes immediately:
   - Drains and flushes all pending audio chunks from the outgoing message queue.
   - Emits an `{"type": "AUDIO_INTERRUPT"}` control frame to the client.
   - Resets internal speaking state: `is_speaking = False`, `is_interrupted = True`.
3. Client-side `useLiveVoice` hook receives `AUDIO_INTERRUPT`:
   - Instantly halts all active `AudioBufferSourceNode` playback sources.
   - Clears scheduled audio buffer queue.
4. Execution completes well within the 150 ms hard SLA.

---

## 4. Session Resumption and Transparent Reconnection

Gemini Live audio sessions encounter provider-side termination near 15 minutes (900 seconds).
To ensure uninterrupted user experience:
1. `VoiceSessionManager` stores session resumption tokens in Redis under `voice:resumption:{workspace_id}:{session_id}`.
2. At the 10-minute (600 seconds) threshold, a transparent session rotation is triggered before the cutoff.
3. The new session inherits conversational context and active task tracking without terminating the client WebSocket.
4. The server emits `{"type": "SESSION_RENEWED", "expires_in": 600}` to inform the client of the extended lease.

---

## 5. Security & Governance Safeguards

- **Tenant Isolation:** WebSocket authentication verifies the caller's JWT access token, resolves the `Principal`, and enforces workspace row-level security.
- **Zero MCP Tools in Voice Context:** Only `submit_task` and `get_task_status` are declared to the model. Attempts to register or execute MCP tools raise `MCPToolRegistrationBlockedError`.
- **Approval Shielding:** Voice can announce pending approvals, recite summaries, and accept cancellations or denials. Any spoken intent to approve is rejected with explicit instructions to confirm in the authenticated web UI.
