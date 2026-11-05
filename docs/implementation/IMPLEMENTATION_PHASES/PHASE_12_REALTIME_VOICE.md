# Phase 12 — Real-Time Voice System

## A. Phase Overview

- **Phase Number:** Phase 12
- **Phase Name:** Real-Time Voice System
- **Objective:** Implement the bidirectional streaming voice subsystem connecting the web client to the Gemini Live API (`gemini-3.8-live`), including client-side WebAudio 16 kHz capture, Silero VAD speech detection, sub-150 ms barge-in interruption handling, transparent session reconnection, and decoupled background task coordination via Redis.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §6 and Decision D3, voice interaction must be decoupled from task execution. Multi-minute background tasks cannot run on the ephemeral audio stream, and the 15-minute Gemini Live session limit must never abort ongoing workflows. The voice model holds exactly two tools (`submit_task`, `get_task_status`) and coordinates asynchronously with the task engine over Redis.
- **Expected Outcome:** A dedicated Voice Service managing bidirectional audio WebSockets; WebAudio `AudioWorklet` streaming 16 kHz PCM; client VAD; barge-in playback termination in <= 150 ms; transparent session resumption (~10-minute token rotation); and voice-to-task handoff enabling Workflow 1.
- **Priority:** High
- **Estimated Complexity:** High
- **Estimated Duration:** 15 business days (Weeks 12–14 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 03 (Gemini AI Integration and Model Gateway), Phase 05 (Orchestrator and Agent Framework), and Phase 06 (Background Task Execution Engine).
- **Required Infrastructure:** Running Redis 7.4; WebSocket support enabled in reverse proxy and backend server.
- **Required Credentials or External Services:** Active `GEMINI_API_KEY` with Gemini Live API access.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D3 (Voice and task execution runtimes decoupled via Redis), Decision D5 (Voice can deny/cancel but never approve), and Decision D12 (p95 latency <= 850 ms, barge-in <= 150 ms).
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md).

---

## C. Scope

### Included Features

- Client-side audio pipeline in Next.js (apps/web/src/hooks/useLiveVoice.ts (planned artifact)):
  - Microphone capture via `AudioWorklet` (16 kHz, 16-bit PCM, mono, 100 ms frames / 1,600 samples).
  - Client-side Voice Activity Detection (Silero VAD) to eliminate silence transmission and conserve tokens.
  - 24 kHz audio playback buffer queue with instant flush on interrupt.
- Backend Voice Service (`backend/app/voice/`):
  - WebSocket endpoint `WS /api/v1/voice/live` bridging client audio to Gemini Live API.
  - Session lifecycle management and transparent resumption token rotation before the 15-minute cutoff.
  - Barge-in handling: on `interrupted: true` or client speech event, emit `AUDIO_INTERRUPT` and flush buffers <= 150 ms.
- Voice-Task Decoupling (Decision D3):
  - Voice model tool declarations: strictly `submit_task(intent, params)` and `get_task_status(task_id)`. Zero MCP tools in voice context.
  - Redis event bridge listening for `TASK_PROGRESS_UPDATE` and injecting spoken progress via `send_client_content`.
- Voice Safety Safeguards (Decision D5):
  - Voice channel can announce approvals, read back details, and accept verbal cancellation or denial.
  - Voice channel is blocked in backend code from granting approvals.
- Enables Acceptance Workflow 1: Voice-initiated daily briefing and schedule recitation.

### Excluded Features

- Speaker diarization or multi-speaker voice biometrics (deferred to post-MVP).
- Native desktop audio worklets (Tauri desktop deferred to post-MVP).

### Deferred Features

- Full duplex video/screen-sharing streaming into Gemini Live API.

### Dependencies on Future Phases

- Phase 14 (Frontend Experience) connects the visual Voice Waveform Orb component to the audio state machine created here.
- Phase 15 runs automated latency benchmarks on the voice roundtrip.

---

## D. Implementation Breakdown

### Task P12-T001: Audio Pipeline and Worklet Module

- **Task ID:** P12-T001
- **Task Name:** Build Client AudioWorklet and VAD Capture Engine
- **Detailed Description:** Create apps/web/public/audio-processor.js (planned artifact) implementing an `AudioWorkletProcessor`. Downsample microphone input to 16 kHz mono 16-bit PCM; emit 100 ms buffers (1,600 samples) via `MessagePort`. Integrate `@ricky0123/vad-web` (Silero VAD) in `useLiveVoice.ts` to detect user speech start and stop events, transmitting audio chunks only when active speech is present.
- **Expected Files:** apps/web/public/audio-processor.js (planned artifact), apps/web/src/hooks/useLiveVoice.ts (planned artifact).
- **Dependencies:** None.
- **Assigned Agent Role:** Audio & Frontend Engineer.
- **Required Tests:** Client unit test verifying PCM conversion and buffer sizing.
- **Documentation Updates:** docs/architecture/VOICE_SUBSYSTEM.md (planned artifact).
- **Acceptance Criteria:** Worklet streams 16 kHz 16-bit PCM chunks; silence frames are suppressed by VAD; memory consumption remains stable during continuous recording.

### Task P12-T002: Backend Voice Service WebSocket Bridge

- **Task ID:** P12-T002
- **Task Name:** Build WebSocket Gateway to Gemini Live API
- **Detailed Description:** Implement backend/app/voice/live_session.py (planned artifact) and WebSocket route in backend/app/api/v1/voice.py (planned artifact). Receive client audio chunks over WebSocket; forward to Gemini Live session (`google-genai` Live API client); stream returned 24 kHz audio chunks back to web client; decode JSON turn complete and interrupt events.
- **Expected Files:** backend/app/voice/live_session.py (planned artifact), backend/app/voice/audio_converter.py (planned artifact), backend/app/api/v1/voice.py (planned artifact).
- **Dependencies:** Phase 03.
- **Assigned Agent Role:** Senior AI Engineer (Realtime).
- **Required Tests:** `pytest backend/tests/integration/test_voice_websocket.py`.
- **Documentation Updates:** docs/architecture/VOICE_SUBSYSTEM.md (planned artifact).
- **Acceptance Criteria:** WebSocket establishes connection; audio streams bidirectionally; audio format translation operates without distortion.

### Task P12-T003: Barge-In and Interruption Protocol

- **Task ID:** P12-T003
- **Task Name:** Implement Sub-150 ms Interruption Handling
- **Detailed Description:** Implement interruption listener in backend/app/voice/live_session.py (planned artifact):
  - When Gemini Live server sends `server_content.interrupted: true` or client VAD reports speech onset during playback, immediately emit WebSocket frame `{"type": "AUDIO_INTERRUPT"}`.
  - Client hook flushes WebAudio playback queue and terminates current source node within 150 ms.
  - Subsequent user speech frames are routed as a new user turn.
- **Expected Files:** backend/app/voice/live_session.py (planned artifact), apps/web/src/hooks/useLiveVoice.ts (planned artifact), backend/tests/unit/test_barge_in.py (planned artifact).
- **Dependencies:** P12-T001, P12-T002.
- **Assigned Agent Role:** Audio Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_barge_in.py` and automated audio latency test.
- **Documentation Updates:** docs/architecture/VOICE_SUBSYSTEM.md (planned artifact).
- **Acceptance Criteria:** Audio playback halts in <= 150 ms upon interruption; buffers clear cleanly without audio artifacts or pops.

### Task P12-T004: Session Resumption and Reconnection Engine

- **Task ID:** P12-T004
- **Task Name:** Implement Transparent Session Reconnection
- **Detailed Description:** Implement session lifecycle manager in backend/app/voice/live_session.py (planned artifact). Store session resumption tokens provided by the Gemini Live API in Redis. At 10 minutes elapsed time (before the 15-minute hard limit), initiate a background parallel session using the resumption token, transition the active audio stream, and terminate the old session without dropping user conversation context.
- **Expected Files:** backend/app/voice/session_manager.py (planned artifact), backend/tests/integration/test_voice_resumption.py (planned artifact).
- **Dependencies:** P12-T002.
- **Assigned Agent Role:** Distributed Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_voice_resumption.py` (simulating 20-minute voice session).
- **Documentation Updates:** docs/architecture/VOICE_SUBSYSTEM.md (planned artifact).
- **Acceptance Criteria:** 20-minute continuous voice session survives 10-minute session rotation transparently; no audio dropouts or lost conversation context.

### Task P12-T005: Voice-to-Task Decoupling and Event Bridge (Decision D3)

- **Task ID:** P12-T005
- **Task Name:** Build Voice Tool Handlers and Redis Event Injector
- **Detailed Description:** Implement tool declarations for the voice model in backend/app/voice/tools.py (planned artifact):
  - `submit_task(intent: str, params: dict) -> dict`: publishes `TASK_SUBMISSION_EVENT` to Redis; speaks immediate confirmation.
  - `get_task_status(task_id: str) -> dict`: queries task status projection.
  - Implement Redis subscriber in backend/app/voice/event_bridge.py (planned artifact): subscribe to `workspace:{ws_id}:events`; on task progress or completion, inject spoken update into the live session via `send_client_content`.
  - Block approval operations from the voice channel (Decision D5).
- **Expected Files:** backend/app/voice/tools.py (planned artifact), backend/app/voice/event_bridge.py (planned artifact), backend/tests/integration/test_voice_task_bridge.py (planned artifact).
- **Dependencies:** P12-T002, Phase 05, Phase 06.
- **Assigned Agent Role:** Senior AI Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_voice_task_bridge.py` (Workflow 1 voice execution).
- **Documentation Updates:** docs/architecture/VOICE_SUBSYSTEM.md (planned artifact).
- **Acceptance Criteria:** Voice submits task; background worker runs independently; spoken progress updates inject into live conversation; voice requests to approve actions are rejected.

---

## E. File and Folder Changes

- **Files to Create:**
  - apps/web/public/audio-processor.js (planned artifact)
  - apps/web/src/hooks/useLiveVoice.ts (planned artifact)
  - backend/app/voice/__init__.py (planned artifact)
  - backend/app/voice/live_session.py (planned artifact)
  - backend/app/voice/session_manager.py (planned artifact)
  - backend/app/voice/audio_converter.py (planned artifact)
  - backend/app/voice/tools.py (planned artifact)
  - backend/app/voice/event_bridge.py (planned artifact)
  - backend/app/api/v1/voice.py (planned artifact)
  - backend/tests/unit/test_barge_in.py (planned artifact)
  - backend/tests/integration/test_voice_websocket.py (planned artifact)
  - backend/tests/integration/test_voice_resumption.py (planned artifact)
  - backend/tests/integration/test_voice_task_bridge.py (planned artifact)
  - docs/architecture/VOICE_SUBSYSTEM.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/voice/`, `apps/web/src/hooks/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. Voice WebSocket Protocol (`WS /api/v1/voice/live`)

- **Client to Server Frames:**
  - Binary: Raw 16 kHz 16-bit linear PCM audio chunk (100 ms / 3,200 bytes).
  - Text JSON:
    ```json
    {"type": "VAD_SPEECH_START"}
    {"type": "VAD_SPEECH_STOP"}
    {"type": "CLIENT_INTERRUPT"}
    ```
- **Server to Client Frames:**
  - Binary: 24 kHz linear PCM audio chunk for immediate playback.
  - Text JSON:
    ```json
    {"type": "TRANSCRIPT_USER", "text": "What is on my schedule today?"}
    {"type": "TRANSCRIPT_MODEL", "text": "Checking your agenda now..."}
    {"type": "AUDIO_INTERRUPT"}
    {"type": "SESSION_RENEWED", "expires_in": 600}
    ```

---

## G. Security Requirements

- **Strict Tool Limitation:** The voice session is restricted to `submit_task` and `get_task_status`. It is barred from holding filesystem, git, or communication tools directly.
- **Approval Shielding:** Voice cannot grant approvals (Decision D5). Spoken intent to approve an action returns: "Approvals must be confirmed in the secure web interface."
- **Audio Privacy:** Audio buffers are ephemeral and held in memory only during active session playback; uncompressed audio chunks are never written to disk without explicit user consent.

---

## H. Testing Strategy

- **Latency Benchmark Tests:** Record automated 16 kHz audio fixture, replay to `/api/v1/voice/live`, measure time delta until first 24 kHz chunk is received; assert p95 <= 850 ms.
- **Barge-In Timing Tests:** Inject simulated user speech packet while model audio is streaming; measure duration until `AUDIO_INTERRUPT` frame is received; assert <= 150 ms.
- **Session Duration Test:** Execute 20-minute audio playback and response simulation; assert transparent session resumption without socket termination.

---

## I. Definition of Done

1. Next.js `AudioWorklet` and Silero VAD capture 16 kHz audio and filter silence.
2. Voice Service connects to Gemini Live API with bidirectional audio streaming.
3. First-audio response latency meets target p95 <= 850 ms.
4. Barge-in flushes audio playback queue in <= 150 ms.
5. Sessions reconnect transparently using resumption tokens past 10 minutes.
6. Task decoupling functions cleanly; voice model triggers background tasks over Redis without blocking audio.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Automated voice benchmark asserts p95 latency <= 850 ms and barge-in <= 150 ms; Workflow 1 (Daily Briefing) executes cleanly via voice instruction.
- **Dependent Phase Unlocked:** Phase 14 (Frontend Experience) is cleared to integrate the full voice canvas.
