# Voice Subsystem (`app.voice`)

The Voice Subsystem provides real-time bidirectional streaming voice capabilities connecting the web client to the Gemini Live API (`gemini-3.8-live`), adhering to Decisions D3, D5, and D12 in `MAYA_AI_FINAL_PLAN.md`.

## Modules

- `audio_converter.py`: PCM16 format conversions, byte-order handling, chunking (100 ms / 1600 samples at 16 kHz), and linear interpolation resampling between 16 kHz and 24 kHz.
- `vad.py`: Voice Activity Detection (VAD) using normalized Root Mean Square (RMS) and energy calculations with hangover smoothing to prevent clipped speech.
- `tools.py`: `VoiceToolRegistry` strictly declaring `submit_task` and `get_task_status`. Enforces Decision D3 (Zero MCP tools in voice context) and Decision D5 (Voice approval shielding; all approvals must be confirmed in the secure web interface).
- `event_bridge.py`: `VoiceEventBridge` subscribing to Redis workspace task events, filtering for the session's active tasks, and translating lifecycle transitions into spoken natural language updates.
- `live_session.py`: `LiveVoiceSession` managing audio frame queues, transcripts, tool dispatch, and sub-150 ms barge-in interruption handling (Decision D12).
- `session_manager.py`: `VoiceSessionManager` tracking session duration, storing resumption tokens in Redis (`voice:resumption:{workspace_id}:{session_id}`), and rotating sessions transparently at 10 minutes before the 15-minute provider hard limit.

## API Route

- `WS /api/v1/voice/live`: WebSocket bridge for bidirectional audio streaming and JSON control frames (`app.api.v1.voice`).
