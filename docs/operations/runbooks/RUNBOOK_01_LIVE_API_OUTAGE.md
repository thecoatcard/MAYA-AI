# Runbook: Gemini Live API Outage & Voice Fallback

- **Owner:** Voice Engineering & SRE Team
- **Last verified:** 2026-11-07 by Production SRE Lead
- **Severity guide:** Sev-1 if global voice connections fail; Sev-2 if voice latency exceeds 850 ms p95 SLA (Decision D12).

---

## Symptoms

1. **Dashboard Alert:** `LiveApiOutage` or `VoiceLatencySLAViolation` firing in Grafana.
2. **Client Errors:** Web client displays `"Voice service temporarily unavailable. Switched to text chat mode."`
3. **Log Signatures:** `WebSocketDisconnect` with code `1011` or `google.genai.errors.APIError: 503 Service Unavailable` appearing in backend logs for route `/api/v1/voice/live`.
4. **Metrics:** `maya_voice_latency_seconds` p95 exceeding 850 ms, or connection drop rate > 5%.

---

## Impact

- Voice input and real-time audio playback are degraded.
- **Task execution and existing running DAGs are NOT impacted** (Decision D3: Voice and task runtimes are strictly decoupled across the Redis event bus).
- Users can continue submitting tasks and chatting via the text interface.

---

## Quick checks

1. Check real-time voice latency and error rate in Grafana dashboard (`docker/grafana/dashboards/maya_overview.json`).
2. Verify Google Cloud status dashboard for regional Vertex AI / Gemini API incidents:
   ```bash
   curl -sI https://generativelanguage.googleapis.com
   ```
3. Check backend container logs for upstream Gemini handshake errors:
   ```bash
   docker logs --tail 100 maya-backend | grep -E "voice_live|google_genai"
   ```

---

## Diagnosis

| Observation | Likely cause | Next step |
| :---------- | :----------- | :-------- |
| HTTP 503 / 502 on Live WebSocket handshake | Google GenAI Live API regional outage | Activate Degraded Text Chat Fallback mode |
| HTTP 429 ResourceExhausted | API Key quota saturation on `live` model alias | Failover to secondary API key or enable rate limiting backoff |
| WebSocket connection timeout > 10s | Network route degradation or proxy buffering | Verify Nginx sticky routing (`ip_hash`) and buffering settings |

---

## Mitigation

### Step 1: Force Client Degradation to Text Chat (Read-only / Config Toggle)
Notify client apps to gracefully disable voice input and present the conversational text canvas. In `maya-backend`, set:
```bash
# Update environment configuration to route voice clients to text fallback
MAYA_VOICE_FORCE_TEXT_FALLBACK=true
```
Clients will seamlessly send text turns and receive streamed markdown responses with optional fallback TTS (`gemini-3.8-flash-tts`).

### Step 2: Switch Model Gateway Voice Fallback Chain (Reversible)
If the primary `gemini-3.8-live` model is unstable, switch the `live` alias to the secondary fallback model `gemini-3.8-live-extended-thinking` or standard bidirectional streaming:
```bash
# Update model overrides in docker-compose.prod.yml
MAYA_GEMINI_MODEL_OVERRIDES='{"live": "gemini-3.8-live-extended-thinking"}'
docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
```

---

## Rollback

Once Google Cloud reports the Live API service is healthy:
1. Revert `MAYA_VOICE_FORCE_TEXT_FALLBACK=false`.
2. Clear model override: `MAYA_GEMINI_MODEL_OVERRIDES='{}'`.
3. Re-deploy backend container:
   ```bash
   docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
   ```
4. Verify voice turn latency returns to p95 <= 850 ms on Grafana.

---

## Escalation

- If outage persists > 30 minutes, escalate to Principal Voice Architect and Google Cloud Enterprise Support.
- Post status update to `#maya-ops` Slack channel.

---

## After the incident

1. Export incident window metrics from Prometheus for postmortem analysis.
2. Verify zero background tasks were aborted or corrupted during the voice outage.
3. Record root cause in `docs/operations/incidents/`.
