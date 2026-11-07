# Voice WebSocket API Specification

> **Endpoint:** `WS /api/v1/voice/live`
> **Protocol:** Bidirectional WebSocket (Binary PCM and JSON Control Frames)

## 1. Authentication and Handshake

Clients authenticate when opening the WebSocket connection by providing an active JWT access token either via query parameter or header:

```text
WS /api/v1/voice/live?token=<ACCESS_TOKEN>
```

Alternatively:
```text
Authorization: Bearer <ACCESS_TOKEN>
```

If authentication fails or the token is missing, the server terminates the connection with code `1008` (`WS_1008_POLICY_VIOLATION`).

---

## 2. Wire Protocol

The connection multiplexes binary audio streams and UTF-8 JSON control messages.

### 2.1 Client to Server Frames

#### Binary Audio Frame
- **Format:** 16-bit linear PCM, signed, little-endian, mono.
- **Sample Rate:** 16,000 Hz (16 kHz).
- **Chunk Size:** 100 ms (1,600 samples / 3,200 bytes).

#### JSON Control Messages

```json
{"type": "VAD_SPEECH_START"}
```
Notifies the backend that client-side VAD has detected user speech onset.

```json
{"type": "VAD_SPEECH_STOP"}
```
Notifies the backend that client-side VAD detected user speech completion.

```json
{"type": "CLIENT_INTERRUPT"}
```
Requests immediate barge-in interruption of any playing or queued model audio.

```json
{"type": "USER_TEXT", "text": "What is on my schedule today?"}
```
Optional text instruction delivered into the live voice session turn.

---

### 2.2 Server to Client Frames

#### Binary Audio Frame
- **Format:** 16-bit linear PCM, signed, little-endian, mono.
- **Sample Rate:** 24,000 Hz (24 kHz).
- **Payload:** Raw PCM bytes destined for immediate playback scheduling in WebAudio.

#### JSON Control Messages

##### User Transcription
```json
{
  "type": "TRANSCRIPT_USER",
  "text": "What is on my schedule today?"
}
```

##### Model Transcription
```json
{
  "type": "TRANSCRIPT_MODEL",
  "text": "Checking your agenda now..."
}
```

##### Barge-In Audio Interruption (Decision D12)
```json
{
  "type": "AUDIO_INTERRUPT"
}
```
Directs client WebAudio engine to flush the playback queue and stop active audio nodes immediately (<= 150 ms).

##### Transparent Session Renewal
```json
{
  "type": "SESSION_RENEWED",
  "expires_in": 600
}
```
Emitted when the backend transparently rotates the Gemini Live session before the 15-minute cutoff.

---

## 3. Disconnection and Termination Codes

| Code | Reason | Description |
| :--- | :--- | :--- |
| `1000` | Normal Closure | Clean shutdown initiated by client or server. |
| `1008` | Policy Violation | Missing or invalid authentication token; expired session. |
| `1011` | Server Error | Unexpected internal server error during streaming. |
