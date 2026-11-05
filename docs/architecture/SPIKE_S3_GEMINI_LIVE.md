# S3: Gemini Live API SDK readiness

## Status and scope

**SDK adapters implemented; bounded user-run live smoke measured.** Both text
methods produced audio in separate sessions. Extended session lifetime and
resumption/context-continuity verification are approved for user execution;
evidence is pending. This is not full live acceptance.

The [S3 harness](../../backend/tests/spikes/spike_s3_gemini_live.py) builds two
independent session plans: `send_client_content` with an explicit user turn and
`turn_complete=True`, and `send_realtime_input(text=...)`. They never share a
session. Each plan has a maximum 960-second observation and compression disabled.
The user-run short smoke exercised `gemini-3.8-live`; availability is observed
for this run only, not guaranteed generally or accepted as a production decision.

## User-run live smoke evidence (2026-11-05)

The user supplied the launcher's redacted JSON, labelled
`user_run_live_measurement` with `status=completed`. The assistant inspected that
file, not credentials, and did not execute the API calls.

| Observation | `send_client_content` | `send_realtime_input` |
| :--- | ---: | ---: |
| First-audio latency | 484 ms | 453 ms |
| Output audio duration | 2,740.042 ms | 1,720.042 ms |
| Audio bytes | 131,522 | 82,562 |
| Observed session duration | 4.359 s | 4.782 s |
| End reason / completed turns | turn_complete / 1 | turn_complete / 1 |

SDK: google-genai 2.34.0. The whole S3/S4 smoke ran from
2026-11-05T06:18:08.744148+00:00 to 06:18:19.817201+00:00 (11.078 seconds).
Three embedding requests preceded the two independent Live sessions.
Retries, reconnections, resumption, tools, grounding and model fallback were off.
These are single observations, not percentiles, quality assessments or thresholds.

The local source filename is
`gemini-free-tier-smoke-20261105T061808.741178Z.json` in the launcher's ignored
results directory. SHA-256:
`c7b8658ecaad9a44153ae182bdd1b4bc3eac4515133f2533c95c4d44aeddd374`.
The summary and fingerprint persist here; the ignored raw file is not shipped
with repository changes. Do not edit the original evidence to change its flags.

The result correctly retains `live_acceptance_verified=false`: lifetime,
resumption/context continuity and provider cost enforcement were not tested.
Free tier/no-linked-billing are user attestations, not independently verified
facts. No paid call was authorized or actual invoice verified.

## Actual SDK adapter

The [isolated Gemini project](../../backend/tests/spikes/gemini/README.md) pins
google-genai 2.34.0 and implements actual typed async SDK operations with injected
clients. It connects each text method independently, validates setup and PCM,
uses monotonic first-audio timing, records usage snapshots, and reconnects on
GoAway using the latest actual resumable handle. Missing/non-resumable updates
invalidate stale handles; reconnects and cleanup are bounded.

Tests use real SDK models but fake clients; they forbid SDK client construction,
credential acquisition and HTTP transport. Provider errors are redacted; cancellation
closes owned async resources. Context continuity and session lifetime remain
unverified. A separately approved hidden-prompt free-tier launcher now provides
the bounded live entry point; the historical offline CLIs remain blocked.

The generated fixture contains 100 ms of 440 Hz, mono little-endian PCM16 at
24 kHz. Its normalized event replay exercises setup, text submission, first audio,
turn completion, GoAway, disconnect, resumption and observation termination.
The analyzer rejects malformed ordering, nonfinite/out-of-bound timestamps,
missing audio, invalid PCM length/rate and incomplete observations.

Fixture timestamps produce a synthetic 125 ms first-audio interval. This value
is test data, **not Gemini performance**. Replaying a resumption event does not
verify provider handles or conversation continuity. Observation timeout and
provider session termination are recorded separately.

## Run offline

From the repository root using the existing backend environment:

```powershell
uv run --directory backend --no-sync python -m tests.spikes.spike_s3_gemini_live --offline
uv run --directory backend --no-sync pytest -q tests\unit\test_spike_s3_gemini_live.py
```

JSON output is labelled `offline_fixture_only`, `live_calls=0` and
`live_acceptance_verified=false`. `--run` exits 2 with an explicit blocker.
The offline CLI does not import the isolated SDK adapter, acquire credentials or
provide a bypass flag. Separate adapters contain SDK calls, not live CLI wiring.

## Approval, cost and lifetime boundaries

### Latest owner decision: extended free-only verification

On 2026-11-05 the owner explicitly approved extending the user-run experiment
to two independent text-method sessions, at most 960 seconds each and at most
four resumptions per session, with bounded synthetic continuity probes.
No tools, grounding, model fallback, billing activation or paid calls are
authorized. The user runs the launcher and enters the key locally; agents do
not acquire credentials or execute requests. Quota/errors stop the experiment.

The owner also explicitly revised Phase 01 acceptance to Free-tier-only with
no linked billing. The paid INR 100 provider-enforced spending cap is no longer
a Phase 01 exit gate, but remains mandatory before any future paid use. This
scope change does not establish a cap or independently verify account billing.
Long-session and real continuity evidence remain pending; finite observations
cannot establish unlimited lifetime. Earlier deferral records below describe
the preceding approvals and are superseded only to this stated extent.

The [extended user-run launcher](../../backend/tests/spikes/gemini/README.md#user-run-extended-continuity-observation)
is implemented and mock-tested. It requests AUDIO plus output transcription,
remembers a synthetic six-word nonce before real GoAway, then requires a complete
normalized word-sequence match after resumed setup. No nonce, transcript, audio
or handle is saved. Local timeout and provider closure are distinct outcomes;
neither mocked success nor elapsed observation proves maximum lifetime.

The owner initially approved live S3/S4 evaluation and isolated SDK installation,
then specified a **combined INR 100 maximum** and requested a full-lifetime cost
estimate first. After reviewing the uncertainty, the owner chose offline harnesses
and deterministic tests only, with live runs blocked until a hard spending cap
can be enforced. The owner subsequently approved actual isolated SDK clients with
mocked tests and compatibility audit, keeping paid execution blocked.
Only the isolated SDK project was installed for validation; runtime pins were unchanged.

During Phase 01 closure, the owner explicitly deferred spending-cap work to the
end/future development and asked that the deferral be documented. This did not
authorize paid calls or replace the INR 100 hard-cap requirement. The live
entry point stays fail-closed; cap enforcement, credential setup and actual S3/S4
measurements are deferred together, not counted as passed acceptance tests.

Official documentation checked on 2026-11-05:

- [Session management](https://ai.google.dev/gemini-api/docs/live-session):
  uncompressed audio-only sessions have a documented 15-minute limit;
  connections last around 10 minutes and may require resumption. Compression
  can extend sessions indefinitely. A finite experiment cannot prove unlimited lifetime.
- [Pricing](https://ai.google.dev/gemini-api/docs/pricing): the listed Gemini 3.8
  Live / Gemini 3.1 Flash Live Preview audio rates were approximately USD 0.005
  per input minute and USD 0.018 per output minute. These are pricing examples,
  not a model-selection decision.
- Two 16-minute observations with continuous input and output illustrate an
  audio-only charge of `32 * (0.005 + 0.018) = USD 0.736`, or INR 66.24 at an
  **assumed**, not verified, INR 90/USD. Text, context reprocessing, S4, taxes
  and exchange-rate variation are extra. This is not an all-in cost estimate.

Billing alerts, local token counters and timeouts are not provider-side hard
spending caps. No such cap is currently verified; no paid run is authorized by
this offline implementation.

## Approved free-tier smoke scope (2026-11-05)

After local closure, the owner confirmed that the intended key's non-production
project is Free tier with **no linked billing account** and approved a narrow
free-tier-only smoke plus a hidden local credential launcher. This is a human
confirmation, not an independently verified account property or programmatic cap.
Paid calls, billing activation, paid fallback and upgrades remain prohibited.

The approved workload is:

- S4: three independent synthetic embedding requests, at most 30 seconds each.
- S3: two independent text-method sessions, at most 60 seconds each, one short
  synthetic prompt/audio response each. Stop on the first completed audio turn.
- At most five minutes total walltime, no retries, reconnects, tools, grounding
  or model fallback. Access/model/quota errors stop the experiment.
- Only the built-in synthetic texts/audio may leave the machine. Free-tier
  inputs/outputs may be used for Google product improvement.
- The user enters the key through a hidden prompt in their own terminal,
  never in chat, command-line arguments, logs or repository files.

For free-supported models on the confirmed unbilled project, the expected API
charge is zero. This does not guarantee model availability or independently
prove that a supplied key belongs to that project. If billing status is unclear
or changes, do not run. Current sources:
[billing](https://ai.google.dev/gemini-api/docs/billing),
[pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

The [safe user-run launcher](../../backend/tests/spikes/gemini/README.md#user-run-free-tier-only-smoke)
is implemented and mock-tested; no agent is authorized to obtain
the key or execute live requests. Even a successful short smoke will not verify
full session lifetime or resumption/context continuity. Those and paid spending
control remain deferred.

## Safe credential preparation and remaining work

1. In [Google AI Studio](https://aistudio.google.com/apikey), use a dedicated
   non-production project and restricted Gemini API access, following
   [Google's key guidance](https://ai.google.dev/gemini-api/docs/api-key).
2. Do not send keys in chat, commit them, place them in examples, or ask an agent
   to read private environment files. This harness does not accept or need keys.
3. Before any paid run, verify provider-side spending enforcement and current
   pricing, then obtain fresh workload approval. The approved free-tier smoke
   is separate and does not waive paid controls.
4. SDK installation remains isolated and pinned. The separately approved free-tier
   launcher obtains credentials through hidden local input, never CLI arguments
   or logs. It requires interactive Free-tier/no-linked-billing confirmations,
   rejects ambient auth/endpoint/proxy overrides and has no paid fallback.
   The older offline `--run` entry points remain blocked.
5. Real first audio and both text methods have short-smoke evidence above.
   GoAway/resumption with context continuity and session-lifetime measurements
   remain deferred; record actual usage and limitations when approved.

No application voice API or runtime integration was added; no architecture
decision was accepted. Live S3 acceptance remains incomplete.

## Deferred development record

| Work item | Current evidence | Revisit before |
| :--- | :--- | :--- |
| Combined S3/S4 spending control | No enforceable INR 100 cap is verified; alerts/timeouts are not substitutes | Any paid Gemini call |
| Safe non-production credentials and live entry point | Owner executed the hidden-prompt Free/no-billing smoke; no key read by agents, account tier not independently verified | Reconfirm scope/billing before another run |
| Real first-audio and text-method acceptance | Both independent sessions returned audio in one user-run smoke (484/453 ms) | Broader workload/quality validation before production use |
| Session lifetime/resumption continuity | Bounded mock lifecycle and handle tests only | Claiming live session recovery or lifetime support |
| Actual model availability, usage and total cost | Model responded in the short smoke; no measured bill or independently verified cap | Selecting production models or claiming budget compliance |

Resume with current official pricing/model checks, verified spending controls,
bounded workload approval and user-managed credentials. Keep the two text methods
in separate sessions; retain real version/config/usage evidence without secrets.
No new target date or unattended live execution is implied by the deferral.
