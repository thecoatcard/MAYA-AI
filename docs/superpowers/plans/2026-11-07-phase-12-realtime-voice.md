# Phase 12 — Real-Time Voice System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement, integrate, test, and certify Phase 12 (Real-Time Voice System) to production standards, including WebAudio 16 kHz capture, Silero/energy VAD speech detection, sub-150 ms barge-in interruption handling, transparent session resumption (~10-minute rotation), decoupled background task coordination via Redis (Decision D3), voice tool limitations (strictly `submit_task` and `get_task_status`, Decision D5), and WebSocket bidirectional streaming (`WS /api/v1/voice/live`).

**Architecture:** A decoupled real-time voice subsystem composed of:
1. Client WebAudio Pipeline: `AudioWorkletProcessor` downsampling microphone to 16 kHz 16-bit mono PCM in 100 ms buffers; client VAD filtering silence; 24 kHz playback queue with instant flush on interrupt;
2. WebSocket Bridge (`/api/v1/voice/live`): Bidirectional binary PCM and JSON protocol handling client speech and model audio;
3. Gemini Live Session Engine (`backend/app/voice/live_session.py`): Real-time streaming session connected to Google GenAI Live API with mock/simulated fallback for testing;
4. Sub-150 ms Barge-In Protocol: Instant `AUDIO_INTERRUPT` frame emission upon client speech onset or model interruption, terminating playback immediately;
5. Session Resumption Manager (`session_manager.py`): Rotates session before the 15-minute hard limit using resumption tokens in Redis;
6. Voice-Task Decoupling & Event Bridge (Decision D3): Voice model holds strictly 2 tools (`submit_task`, `get_task_status`). Zero MCP tools in voice context. Redis subscriber listens to task progress and injects spoken updates via `send_client_content`.
7. Voice Safety Gate (Decision D5): Voice channel cannot approve actions; any spoken approval intent is blocked and redirected to the web interface.

**Tech Stack:** Python 3.12, FastAPI, WebSockets (`starlette.websockets`), Redis 7.4, Google GenAI SDK (`google-genai` Live API), Next.js 15, Web Audio API (`AudioWorklet`), Pytest, Vitest.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§6, §12, §15, §19, Decisions D3, D5, D12), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_12_REALTIME_VOICE.md`.

## Global Constraints

- **Decision D3 (Voice-Task Decoupling):** Voice model is strictly decoupled from task execution runtimes. Voice model possesses exactly two tools: `submit_task(intent, params)` and `get_task_status(task_id)`. Zero MCP tools in voice context.
- **Decision D5 (Voice Approval Shielding):** Spoken PINs or approvals are prohibited; voice channel cannot approve (`403 Forbidden` / redirection to authenticated web UI). Voice can announce, read back, and cancel/deny.
- **Decision D12 (NFR Latency & Interruption):** SLA p95 first-audio <= 850 ms (target 450–600 ms); barge-in interrupt latency <= 150 ms.
- **Transparent Session Resumption:** Session rotated at 10 minutes using resumption tokens before the 15-minute cutoff without loss of context.
- **Audio Privacy:** Audio buffers are ephemeral and held in memory only during active playback; raw audio is never persisted to disk without explicit user consent.
- **Zero Regressions:** All existing 1,842+ backend tests, 1,066 frontend tests, and monorepo governance checks must pass.

---

### Task 1: Audio Converter and VAD Module

**Files:**
- Create: `backend/app/voice/audio_converter.py`
- Create: `backend/app/voice/vad.py`
- Test: `backend/tests/unit/test_audio_converter.py`
- Test: `backend/tests/unit/test_voice_vad.py`

**Interfaces:**
- `AudioConverter`: `pcm16_to_samples(raw_bytes: bytes) -> list[int]`, `samples_to_pcm16(samples: list[int]) -> bytes`, `chunk_pcm16(data: bytes, chunk_duration_ms: int = 100, sample_rate: int = 16000) -> list[bytes]`, `resample_pcm(data: bytes, from_rate: int, to_rate: int) -> bytes`.
- `VoiceActivityDetector`: `is_speech(frame_bytes: bytes, threshold_energy: float = 0.015) -> bool`, `process_frame(frame: bytes) -> VADState`.

- [ ] **Step 1: Write unit tests for audio conversions, chunking, byte-order, and VAD speech/silence detection**
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement `backend/app/voice/audio_converter.py` and `backend/app/voice/vad.py`**
- [ ] **Step 4: Run tests to verify 100% pass rate**

---

### Task 2: Voice Tools and Decoupled Task Bridge (Decisions D3 & D5)

**Files:**
- Create: `backend/app/voice/tools.py`
- Create: `backend/app/voice/event_bridge.py`
- Test: `backend/tests/unit/test_voice_tools.py`
- Test: `backend/tests/integration/test_voice_task_bridge.py`

**Interfaces:**
- `VoiceToolRegistry`: strictly defines `submit_task` and `get_task_status`. Blocks any attempt to register MCP tools or execute approval actions (`VoiceApprovalProhibitedError`).
- `VoiceEventBridge`: subscribes to Redis `workspace:{workspace_id}:events`, filters events for the session's active tasks, formats spoken progress announcements, and injects them into the live session.

- [ ] **Step 1: Write unit tests asserting tool declarations, parameter validation, and approval denial behavior**
- [ ] **Step 2: Write integration tests verifying Redis task event subscription and spoken notification queueing**
- [ ] **Step 3: Implement `tools.py` and `event_bridge.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 3: Live Session Engine and Sub-150 ms Barge-In Protocol (Decision D12)

**Files:**
- Create: `backend/app/voice/live_session.py`
- Test: `backend/tests/unit/test_barge_in.py`
- Test: `backend/tests/unit/test_live_session.py`

**Interfaces:**
- `LiveVoiceSession`: manages bidirectional audio stream with Gemini Live API (and mock client for test environments).
  - Handles client binary PCM audio frames.
  - Generates model 24 kHz PCM audio frames.
  - On `interrupted: true` or client `CLIENT_INTERRUPT` frame, immediately flushes outgoing audio buffer and emits `{"type": "AUDIO_INTERRUPT"}` in <= 150 ms.
  - Executes voice tools `submit_task` and `get_task_status`.

- [ ] **Step 1: Write unit tests verifying barge-in timing, interrupt frame dispatch, and buffer flushing**
- [ ] **Step 2: Implement `LiveVoiceSession` in `backend/app/voice/live_session.py`**
- [ ] **Step 3: Run unit tests to verify they pass**

---

### Task 4: Session Resumption and Reconnection Manager

**Files:**
- Create: `backend/app/voice/session_manager.py`
- Test: `backend/tests/integration/test_voice_resumption.py`

**Interfaces:**
- `VoiceSessionManager`: maintains session state and resumption tokens in Redis (`voice:resumption:{workspace_id}:{session_id}`).
  - Tracks session age; triggers transparent parallel reconnection at 10 minutes (600s) before 15-minute cutoff.
  - Seamlessly migrates active audio stream without dropping user dialogue context.

- [ ] **Step 1: Write integration tests verifying session resumption token persistence, expiration, and transparent rotation**
- [ ] **Step 2: Implement `VoiceSessionManager` in `backend/app/voice/session_manager.py`**
- [ ] **Step 3: Run tests to verify they pass**

---

### Task 5: Voice WebSocket REST/WS Endpoint

**Files:**
- Create: `backend/app/api/v1/voice.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/integration/test_voice_websocket.py`

**Interfaces:**
- `WS /api/v1/voice/live`: Authenticates JWT via query param or headers, resolves workspace RLS, spawns `LiveVoiceSession`, bridges incoming/outgoing audio frames and JSON control messages, handles disconnection and clean cleanup.

- [ ] **Step 1: Write WebSocket integration tests covering auth, audio streaming, VAD events, barge-in, and disconnect cleanup**
- [ ] **Step 2: Implement `backend/app/api/v1/voice.py` and register in `router.py`**
- [ ] **Step 3: Run WebSocket integration tests against local test server**

---

### Task 6: Client WebAudio Worklet and React Hook

**Files:**
- Create: `apps/web/public/audio-processor.js`
- Create: `apps/web/src/hooks/useLiveVoice.ts`
- Create: `apps/web/src/hooks/useLiveVoice.test.ts`

**Interfaces:**
- `audio-processor.js`: `AudioWorkletProcessor` downsampling input to 16 kHz 16-bit PCM in 100 ms buffers.
- `useLiveVoice`: React hook returning `{ isConnected, isSpeaking, isListening, startSession, stopSession, interruptPlayback, transcripts, activeTask }`.

- [ ] **Step 1: Write Vitest tests for `useLiveVoice` state transitions, audio queueing, and barge-in flush**
- [ ] **Step 2: Implement `audio-processor.js` and `useLiveVoice.ts`**
- [ ] **Step 3: Run Vitest tests to verify 100% pass rate**

---

### Task 7: Architecture Documentation & Verification

**Files:**
- Create: `docs/architecture/VOICE_SUBSYSTEM.md`
- Create: `docs/api/VOICE_SPECIFICATION.md`
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Author `docs/architecture/VOICE_SUBSYSTEM.md` documenting WebAudio architecture, barge-in, decoupling, and token rotation**
- [ ] **Step 2: Author `docs/api/VOICE_SPECIFICATION.md` documenting WebSocket binary and JSON protocols**
- [ ] **Step 3: Update `docs/implementation/IMPLEMENTATION_STATUS.md` and `CHANGELOG.md`**
- [ ] **Step 4: Validate documentation with `npm run docs:check`**

---

### Task 8: Full Regression & Integration Verification

- [ ] **Step 1: Run `ruff check backend` and `ruff format --check backend`**
- [ ] **Step 2: Run `mypy .`**
- [ ] **Step 3: Run full backend pytest suite**
- [ ] **Step 4: Run frontend Vitest suite**
- [ ] **Step 5: Run monorepo fast validation (`npm run validate:fast`)**
