# S4: Embedding dimensionality SDK readiness

## Status and interfaces

**User-run live smoke verified three 768-dimensional responses.** SDK-model
tests remain deterministic; measured values below are actual user-run evidence,
not the hand-constructed fixtures. No semantic-quality threshold is asserted.

## User-run live measurements (2026-11-05)

The completed [S3/S4 smoke evidence](SPIKE_S3_GEMINI_LIVE.md#user-run-live-smoke-evidence-2026-11-05)
used google-genai 2.34.0 and `gemini-embedding-2`, with three separate requests
configured for 768 dimensions and no retries or fallback:

| Vector | Dimensions | Raw L2 norm | Maximum local float32 roundoff |
| :--- | ---: | ---: | ---: |
| First synthetic text | 768 | 0.9999999348112885 | 4.978828432866678e-09 |
| Related synthetic text | 768 | 1.0000001695196645 | 3.675746915177136e-09 |
| Unrelated synthetic text | 768 | 0.999999852816136 | 3.6323738045185294e-09 |

Related-pair cosine: 0.626323217770136; unrelated-pair cosine:
0.24086029776836865. Local normalization produced norms 1.0.
Raw values were near unit length before local normalization. This is a tiny
synthetic comparison, not a retrieval-quality evaluation or accuracy guarantee.
Float32 roundoff is local conversion error, not provider-internal precision.
Billable-character count was absent (`null`); no billed cost was established.

The S4 request/shape observations are verified within this run. The combined
report still has `live_acceptance_verified=false` because the smoke does not
establish full S3 lifetime/resumption, independent billing status or paid caps.

The [S4 harness](../../backend/tests/spikes/spike_s4_embeddings.py) supplies a
Python SDK keyword plan for `gemini-embedding-2` with
`config={"output_dimensionality": 768}` and three synthetic text inputs: one
related pair and one unrelated comparison. This is not a REST JSON body.

The [isolated SDK adapter](../../backend/tests/spikes/gemini/README.md) now
awaits actual `models.embed_content` calls using
`types.EmbedContentConfig(output_dimensionality=768)`. It sends each text in a
separate request: Gemini Embedding 2 aggregates list inputs, so the offline
three-text bundle is a workload plan, not a single live request. Each request
must return exactly one vector; all three are validated by the existing analyzer.
Optional billable-character counts are retained only when all responses provide
them. Failures are redacted and the injected async client is closed.

The analyzer accepts a decoded SDK-style response object containing `embeddings`,
with one `values` list per input. It requires exactly 768 finite numeric values
per vector and rejects booleans, missing/extra embeddings, zero norms and values
not representable as float32. It records:

- Returned dimensions and raw L2 norms, without modifying input values.
- Norms after local normalization and cosine similarities for the two pairs.
- Maximum absolute roundoff from a local float32 conversion. This does **not**
  reveal the model's internal numeric precision.

The generated vectors are hand-constructed to verify the arithmetic. Their norms
and similarities are fixture values, not semantic model quality. There is no
invented similarity or normalization acceptance threshold.

## Run offline

```powershell
uv run --directory backend --no-sync python -m tests.spikes.spike_s4_embeddings --offline
uv run --directory backend --no-sync pytest -q tests\unit\test_spike_s4_embeddings.py
```

Output is labelled `offline_fixture_only`, `live_calls=0` and
`live_acceptance_verified=false`. `--run` fails with exit 2 before credential
access. It does not import the isolated SDK project or acquire credentials.
The SDK adapter is separately pinned and mock-tested without network calls.

## Documented API contract, not live proof

[Google's embedding guide](https://ai.google.dev/gemini-api/docs/embeddings),
checked on 2026-11-05, documents the Python SDK
`EmbedContentConfig(output_dimensionality=768)` parameter and automatic
renormalization of truncated Gemini Embedding 2 dimensions. It also says not to
use `task_type` for this model; this request plan does not include that field.
Future measurements must still record actual dimensions/norms rather than infer
them from those docs or silently normalize away deviations.

## Live blockers and next steps

The owner's latest scope permits SDK-client implementation and mocked tests,
but blocks paid execution until a hard cap within the combined
INR 100 S3/S4 budget can be enforced. See the [S3 readiness report](SPIKE_S3_GEMINI_LIVE.md)
for the cost illustration, caveats and safe user-managed credential preparation.
S4 pricing and total charge have not been independently estimated or measured.
Provider budgets/alerts must not be presented as guaranteed caps.

For Phase 01 closure, the owner asked to leave capping for the end/future
development and document it. The [deferred development record](SPIKE_S3_GEMINI_LIVE.md#deferred-development-record)
covers both spikes. No paid-call authorization, cap waiver or completed live
acceptance was inferred at that point. The subsequent bounded user-run smoke
now verifies model access, 768-dimensional outputs, norms and pair scores above;
actual billing and broader semantic evaluation remain unverified.

The owner subsequently confirmed a Free-tier project with no linked billing and
approved the [narrow free-tier smoke](SPIKE_S3_GEMINI_LIVE.md#approved-free-tier-smoke-scope-2026-11-05):
three synthetic embedding requests, at most 30 seconds each, without retries or
paid/model fallback. This user-run exception does not authorize paid execution
or by itself establish live results. The [hidden credential launcher](../../backend/tests/spikes/gemini/README.md#user-run-free-tier-only-smoke)
is implemented and mock-tested; the user has now returned completed evidence,
summarized above.

Future runs must reconfirm the approved unbilled scope or resolve paid controls,
and record SDK/model versions, usage, timestamps and redacted results. No real
documents or repository content should be sent; synthetic texts suffice.

No runtime gateway, application index, database migration or vector-storage
decision was added. No new ADR is needed for this evidence update. S4's bounded
parameter/shape measurements do not complete the remaining S3 live criteria.
