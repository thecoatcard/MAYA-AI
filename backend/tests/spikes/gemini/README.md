# Isolated Gemini SDK preparation (Phase 01 S3/S4)

**Implemented SDK adapters and narrowly scoped user-run free-tier launchers;
mock-tested extended continuity; user-run short-smoke evidence exists.** No model request, credential
acquisition, or real SDK client construction was performed by the agent. The
Phase 01 scope excludes the paid spending-cap work. Paid use remains prohibited
until a separately reviewed enforceable hard spending cap and fresh owner
approval are in place. Timeouts and usage counters are not spending controls.

## Boundary and dependency audit

This subproject alone pins `google-genai==2.34.0`; its `uv.lock` locks all resolved
dependencies. The backend runtime manifest/lock and installed runtime environment
do not include the SDK. No application import points into this project.

Public [PyPI metadata](https://pypi.org/pypi/google-genai/2.34.0/json), verified on
2026-11-05, requires Python >=3.11 and Pydantic >=2.12.5,<3. This project targets
the repository's Python 3.12. The isolated install resolved Pydantic 2.14.2,
HTTPX 0.28.1, google-auth 2.62.1 and websockets 17.4. Resolver-only compatibility
with the current backend runtime pins was also verified without installing into
that runtime:

```powershell
uv pip compile backend\pyproject.toml backend\tests\spikes\gemini\pyproject.toml --python-version 3.12 --no-annotate
```

Result: 37 runtime-plus-SDK packages resolved with the existing backend pins
unchanged. This is resolver evidence, not runtime integration or a supply-chain
security certification. The isolated development lock resolves 39 packages
(38 installed, excluding the non-packaged project itself).

## Implemented SDK operations

`gemini_spike/adapters.py` takes injected async clients. Strict structural typing
checks the real `google.genai.client.AsyncClient` against these interfaces.
The isolated project has separate short-smoke and extended-continuity launchers,
apart from the dependency-free offline CLIs. Each asks for two interactive scope
confirmations
before hidden local key input and explicitly constructs the Developer API client.
The launcher closes both SDK clients; the adapters still take injected async
clients. Neither the agent nor the offline host CLIs acquire credentials or make
model requests.

- **S4:** `embed_synthetic` awaits real `models.embed_content` operations using
  `types.EmbedContentConfig(output_dimensionality=768)`, model
  `gemini-embedding-2`, and only the three built-in synthetic texts. It sends
  **one text per request** because Embedding 2 aggregates list inputs. Exactly one
  embedding must be returned per request. The smoke bounds each request to 30
  seconds and all S4 work to 60 seconds. The existing
  [S4 analyzer](../spike_s4_embeddings.py) checks all three raw vectors together
  for dimensions, finite/nonzero norms, float32 representability and similarity.
  Returned values are not silently normalized before analysis. Optional
  billable-character counts are summed only when all three responses supply them.
- **S3:** `observe_pair` uses independent connections for
  `send_client_content(turns=types.Content(...), turn_complete=True)` and
  `send_realtime_input(text=...)`. Model `gemini-3.8-live` is a documented candidate,
  **not live-verified availability or acceptance**. Typed Live config requests
  AUDIO, session resumption, and no context compression. A missing setup
  confirmation fails before sending. Only the built-in synthetic prompt is accepted.
- The dedicated smoke helper uses a distinct, stricter S3 mode: two separate
  sessions, at most 60 seconds each, no session-resumption config or reconnect
  path, and it closes the receive stream immediately after the first completed
  audio turn. Output is bounded to 10 seconds per session. Existing historical
  observation/resumption behavior and its tests are unchanged.
- `observe_continuity_session` is used only by the extended free-tier launcher.
  It requests the already smoke-tested AUDIO modality with output-audio
  transcription enabled, and no tools/grounding. It seeds six synthetic,
  pronounceable words before GoAway, then asks the model to say them only after
  a connection setup using the actual resumable handle. In-memory transcription
  is compared as a complete normalized word sequence after both transcription
  completion and model-turn completion; punctuation/case/spacing normalization
  cannot accept partial or substring matches. Audio is validated as
  `audio/pcm;rate=24000`, counted, and bounded to 30 seconds per session;
  transcription is bounded to 256 UTF-8 bytes per session. Raw audio and
  transcription are never persisted or included in observations.
- The extended launcher runs the two different text-input methods in independent
  sessions, each bounded to 960 seconds and four resumptions. Responses use AUDIO
  with output-audio transcription, not TEXT modality. It continues
  receiving after completed turns; a completed turn is not treated as session
  lifetime. Evidence distinguishes observed GoAway/handle/resumed setup,
  normal provider closure, abnormal provider disconnect (numeric close code),
  the local observation timeout, and the resumption limit. It always leaves
  `live_acceptance_verified` false: a local timeout is not provider closure or
  proof of maximum lifetime.
- Optional synthetic input is 100 ms of 440 Hz, mono little-endian PCM16 at 16 kHz,
  sent as `types.Blob` and followed by `audio_stream_end=True`. Output must be
  nonempty, even-byte PCM with MIME `audio/pcm;rate=24000`. All inline audio parts
  are validated and counted; output payloads/transcripts are not retained or played.
  First-audio latency uses a monotonic clock starting before the text send.
- SDK `receive()` stops after a turn, not after the session. The adapter receives
  again after each turn and records GoAway and usage snapshots. Usage snapshots
  are not summed because counters may be cumulative.
- GoAway closes the old connection before reconnecting with the **actual latest
  resumable handle**. A non-resumable or handle-less update invalidates an older
  handle. Handles never enter the result or logs. Inputs are not replayed after
  resumption. At most four reconnects are attempted within the original deadline.
- Historical connect, send, receive and reconnect share the plan's walltime bound (at most
  960 seconds per probe). Socket and async-client cleanup each have a separate
  2-second bound and are awaited on cancellation; cleanup failure is not reported
  as successful observation. Receive generators close explicitly on GoAway,
  timeout and cancellation.
- Local observation timeout, provider normal closure, GoAway without a handle,
  and reconnect exhaustion have distinct end reasons. The installed SDK turns
  WebSocket closures into `APIError`; only receive-stage 1000/1001 codes count
  as normal provider closure. Other connect/send/receive/validation failures
  raise a fixed redacted `AdapterFailure` without raw provider exception text.

## Mocked validation

From the repository root, install **only this isolated project**, subject to the
owner's dependency-install approval:

```powershell
uv sync --directory backend\tests\spikes\gemini --locked
uv run --directory backend\tests\spikes\gemini --no-sync pytest -q
uv run --directory backend\tests\spikes\gemini --no-sync mypy
uv run --directory backend\tests\spikes\gemini --no-sync ruff check .
uv run --directory backend\tests\spikes\gemini --no-sync ruff format --check .
```

Checks use real SDK configuration/response models and injected fake clients,
never a real transport. An autouse fixture forbids SDK client constructors,
credential acquisition, HTTPX sends and SDK WebSocket connects. Signature/type
checks inspect the installed SDK without constructing clients. Expiry tests schedule event-loop
timeouts, not sleeps or timing thresholds. The cases include malformed/empty
vectors, extra vectors, partial request failure, invalid PCM, failed connect/send/
receive/close, missing setup, cancellation, bounded cleanup, both text methods,
normal versus abnormal provider closure, latest-handle replacement/invalidation,
and bounded reconnects. The extended continuity checks validate the complete
nonce transcription after resumed setup, continued observation beyond a
completed turn, four-resumption termination, normal/abnormal provider closure,
timeout distinction and redaction. Launcher checks cover interactive confirmation
ordering, environment override refusal, fixed endpoint and retry settings, full
fake-client success, timeout/quota failures, cleanup of both clients, evidence
redaction, and the help/noninteractive CLI paths. All tests avoid SDK client
construction and network calls. A fresh-process import tripwire proves both
offline CLIs reject `--run` without importing the SDK, adapters or credential
loaders.

SDK-only checks deliberately use names `checks/sdk_*_checks.py` and their own
pytest configuration, so the backend's ordinary pytest discovery does not collect
them. Isolated strict mypy checks all six source files: the adapter, both
launchers, package initializer, SDK-only checks and their conftest. Its configured roots are
`gemini_spike` and `checks`, with no broad suppressions, import ignores or
exclusions.

The parent has persisted the precise backend mypy exclusion
`^tests/spikes/gemini/` alongside the unchanged S1 exclusion in the shared backend
configuration. This keeps SDK-only files in their separately locked strict check,
while host orchestration, offline CLIs and deterministic backend tests remain in
the default backend check.

Earlier baseline validation recorded on 2026-11-05 before the extended launcher:

- Isolated pytest: **65 passed**; strict mypy: **4 source files passed**;
  Ruff lint and format checks passed.
- `uv run --directory backend --no-sync pytest -q`: **140 passed** with no
  installed runtime `google-genai`.
- Backend Ruff lint/format passed. Before parent integration, strict backend
  typing passed for 30 files using this precise command-line boundary:

  ```powershell
  uv run --directory backend --no-sync mypy --exclude '^tests/spikes/(gemini/|s1/(driver|runtime)\.py$)' .
  ```

Extended-launcher validation using the existing isolated environment and
`--no-sync`:

- Isolated pytest: **100 passed**; strict mypy: **6 source files passed**;
  Ruff lint and format checks passed.
- The extended launcher `--help` and existing short-smoke `--help` both passed.
- No live API command was run; no credential was acquired and no network request
  was made by the agent.
- Root `npm run validate:fast`: **7 passed, 1 failed**; `npm run validate`:
  **8 passed, 2 failed, 4 skipped**. Repository/documentation and web checks
  passed; backend stack checks could not find global `pytest`, `ruff` or `mypy`
  on PATH. The isolated checks above ran through the existing project
  environment; no install or sync was performed.

- `npm run validate:fast` and `npm run validate` were run **before parent
  integration**. That historical full-validation run (with backend tools on PATH
  and Next telemetry disabled) reported **8 passed, 2 failed, 4 skipped**:
  shared planned-document links failed documentation checks, and backend mypy
  entered the SDK-only project before the precise exclusion was persisted.
  Governance unit tests (37) and stack tests/build passed. Range checks were
  skipped for lack of a comparison base. These historical failures do not
  describe the post-integration default checks. The parent owns the combined
  default-validation rerun; no post-integration result is claimed here.
- The new bounded-socket-cleanup test initially failed because cleanup expiry
  was classified as observation timeout; after fixing that distinction, it
  passes. This establishes that the negative test detects the failure.
- After implementing the user-run launcher, isolated validation reported
  **85 pytest checks passed**, strict mypy passed for **5 source files**, Ruff
  lint passed, and Ruff formatting passed. The launcher CLI help and noninteractive
  refusal are also covered by subprocess tests. No API request was made.

The existing offline tools remain dependency-free, labelled synthetic, with
zero live calls and no accepted live results. Their `--run` paths unconditionally
exit 2 before any SDK import, client construction or credential access. There is
no budget-approval boolean, callback or bypass switch.

## User-run free-tier-only smoke

The key's project tier and lack of linked billing are **user attestations**, not
programmatically verified facts. The launcher does not enforce a provider-side
cost cap; it is not permission for paid calls, billing activation, model fallback,
or a future paid run. Paid use remains out of scope and requires a separately
reviewed enforceable hard spending cap plus fresh owner approval. If project
billing status is unclear or changes, do not run.

From the repository root in an interactive PowerShell terminal:

```powershell
uv sync --directory backend\tests\spikes\gemini --locked
uv run --directory backend --project tests\spikes\gemini --no-sync python -m tests.spikes.gemini.gemini_spike.free_tier_smoke --help
uv run --directory backend --project tests\spikes\gemini --no-sync python -m tests.spikes.gemini.gemini_spike.free_tier_smoke
```

The first command recreates the isolated environment if it was removed after
validation; it does not install dependencies into the backend project.

The non-help launcher command prints the complete scope, then requires separate
exact `YES` confirmations for Free tier and no linked billing before using
hidden local `getpass` input. It has no API-key argument or paid/bypass flag.
Noninteractive invocation refuses before asking for a key. The launcher
rejects known SDK auth/endpoint/client-mode overrides, HTTP/WebSocket proxy
settings and HTTPX certificate overrides without printing their values. It
pins `vertexai=False`, the official Developer API URL and `v1beta`; SDK HTTP
attempts are set to one,
HTTPX transport retries to zero, HTTPX ambient environment is disabled, and the
pinned SDK WebSocket connector is explicitly given `proxy=None`.

The run sends three independent built-in S4 texts, followed by two independent
built-in S3 text probes that request one audio turn. The total operation deadline
is 285 seconds, leaving bounded cleanup within the five-minute scope. Model,
access, quota and timeout failures stop without retry or fallback. Results
default to a timestamped JSON under
`backend\tests\spikes\gemini\results\`; `--output-dir` selects another safe
location. The default `results/` directory is ignored by Git. The JSON contains
only status, bounded numeric measurements, SDK/model
identifiers and explicit limitations. Failure JSON remains `failed` and may
include safe partial measurements; no key, prompt, audio, raw exception, response
headers, session handle or transcript is saved. The client uses an explicit API
key rather than ambient Developer API keys or Vertex/ADC credentials and closes
both sync and async SDK clients.

A `completed` smoke only means these bounded requests returned valid responses.
It does not prove account tier, zero billing, model availability in general,
full session lifetime, resumption/context continuity or a paid-cost limit. The
selected Live model responded in the user-run short smoke; general availability
and full lifecycle acceptance remain unverified.

## User-run extended continuity observation

This distinct launcher performs no embedding requests or audio-input uploads;
it requests bounded AUDIO output and transcription for synthetic text. From the
repository root in an interactive PowerShell terminal, first inspect help, then
start the run only if the selected Developer API project is Free and has no
linked billing account:

```powershell
uv run --directory backend --project tests\spikes\gemini --no-sync python -m tests.spikes.gemini.gemini_spike.free_tier_continuity --help
uv run --directory backend --project tests\spikes\gemini --no-sync python -m tests.spikes.gemini.gemini_spike.free_tier_continuity
```

The second command requires exact `YES` attestations for Free tier and no linked
billing before requesting the API key through hidden local input. It has no
API-key argument, no paid/bypass option, no retry/fallback, and stops on errors.
It sends only built-in synthetic text over the pinned Developer API client.
Do not run if the project billing state is unknown or has changed.

Each of the two text-input sessions runs sequentially for at most 960 seconds,
with at most four resumptions. The overall local bound is 1,948 seconds
(32 minutes 28 seconds), including bounded cleanup. After a real GoAway with a
resumable handle, the launcher reconnects and records whether setup completed;
it then asks the model to say the nonce seeded before reconnect. The Live
configuration uses `response_modalities=[AUDIO]` plus
`output_audio_transcription`; it does not request TEXT modality. The emitted
PCM and transcription are held in memory only, with 30 seconds of PCM
(`audio/pcm;rate=24000`) and 256 UTF-8 transcript bytes maximum per session.
The entire normalized word sequence must match after both transcription and
model-turn completion; punctuation/case/spacing normalization does not accept
partial or substring matches. Receiving continues
after turn completion until provider closure, the local 960-second observation
limit, or the resumption bound. The total walltime bound, including bounded
socket/client cleanup, is recorded in the evidence.

Interpret the redacted `results/gemini-free-tier-continuity-*.json` as follows:

- `status: "completed"` means both sessions passed the post-resumption nonce
  check and reached provider closure or the local observation bound. It is not
  lifetime acceptance; `live_acceptance_verified` remains false.
- `context_continuity_verified: true` means the exact six-word synthetic nonce
  was present as the complete transcription after a resumed setup; it does not
  certify general conversation quality or unlimited retention.
- `provider_closed: true` and `end_reason: "provider_closed"` indicate normal
  provider closure. `provider_disconnected` records an abnormal provider close
  separately and may include its numeric close code.
- `end_reason: "observation_timeout"` and
  `observation_limit_reached: true` mean the local bound expired; this is not
  provider closure and is not a measurement of the provider's maximum lifetime.
- `resumption_limit`, missing continuity, or provider disconnect causes a
  failed run and stops further sessions. `live_acceptance_verified` remains
  false in every outcome; this launcher does not report lifetime acceptance.

The launcher has no provider-side cost cap and authorizes no paid use. Any
future paid use still requires a separately reviewed enforceable hard spending
cap and fresh owner approval.

## Evidence and remaining limitations

Official references and installed source checked on 2026-11-05:

- [Python SDK documentation](https://googleapis.github.io/python-genai/genai.html)
  and installed `genai/live.py`, `models.py`, `types.py`, `errors.py`.
- [Embedding guide](https://ai.google.dev/gemini-api/docs/embeddings):
  Embedding 2 aggregation requires independent text calls for this comparison.
- [Live capabilities](https://ai.google.dev/gemini-api/docs/live-guide):
  text methods, audio formats and multipart responses.
- [Session management](https://ai.google.dev/gemini-api/docs/live-session):
  GoAway, resumption handles and finite versus compressed sessions.

**Not tested by the agent:** real model availability, network wire behavior,
actual audio quality/latency, billable usage, real context continuity after
resumption, provider session lifetime or cost. Deterministic fake-client tests
exercise the AUDIO/transcription config, exact nonce check, byte bounds and
lifecycle logic only. Cancellation persistence failure emits a fixed redacted
warning. A normal connection closure
is not proof of a model's maximum session lifetime. The short smoke always leaves
`context_continuity_verified` false; the extended launcher sets it true only for
an exactly matching post-resumption nonce. `live_acceptance_verified` remains
false in either launcher. No actual success report is fabricated.

Any future **paid** execution requires a separately reviewed, enforceable hard
spending cap and fresh owner approval; alerts, estimates, local counters and
timeouts are not hard caps. Shared architecture reports, testing documentation and
changelog are maintained by the parent task. Do not remove the isolated `.venv`
or other pre-existing files as part of this bounded implementation.
