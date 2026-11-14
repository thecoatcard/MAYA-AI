# Change report: Gemini chat context caching

## Summary

**Status: Partially implemented; not tested.** Chat already uses fixed system instruction constants and a fixed optional task tool declaration before request-specific contents. Added an integration test for stable prompt prefixes and documented explicit context caching as deferred because provider minimum-size eligibility could not be established offline.

## Findings

- **1a — cache manager usage:** `ContextCacheManager` is defined in `backend/app/gateway/cache_manager.py:28`. Searches found no reference from `ModelGateway` (`backend/app/gateway/model_gateway.py:514`, `:627`, `:883`), chat service (`backend/app/services/chat_service.py:730`), or `backend/app/api/v1/chat.py`. The chat path calls `generate_stream` at `chat_service.py:730`; no explicit-cache operation occurs.
- **1b — SDK and prefix:** Installed SDK source has synchronous and asynchronous `caches.create` methods in `backend/.venv/Lib/site-packages/google/genai/caches.py:1125` and `:1582`, and `UsageMetadata.cached_content_token_count` at `types.py:20702` and `:20709`. The SDK source does not state the provider minimum token count. It was not possible to verify provider account/project ownership, model restrictions, minimum size or pricing offline. Chat instructions are fixed constants in `backend/app/services/context_manager.py`; the optional task declaration is the fixed `TASK_TOOL` in `backend/app/services/chat_service.py`. Request-specific text and history remain in contents.
- **1c — key label and stale-cache recovery:** `KeyPool` has non-reversible labels (`backend/app/gateway/key_pool.py`, `KeyEntry.label`); current `ContextCacheManager.make_cache_key` has workspace/model/content hash but no key label (`cache_manager.py:34`). The manager can purge a stale reference (`cache_manager.py:97`), but no gateway path catches a cached-name 404/permission failure or retries uncached. Since caches are not created or attached by the gateway, key-aware cache entries and fallback behavior were not added. If explicit caching is enabled later, the cache key and stale-reference handling must include the selected key label and retry uncached after purging.

## Changes

- `backend/tests/integration/test_chat_streaming.py`: added a two-request fake-client check that the system instruction and tool declarations remain equal while request contents differ.
- `docs/architecture/MODEL_GATEWAY.md`, `docs/guides/CONFIGURATION.md`: documented prefix stability, current cache-manager boundary and why explicit caching has no setting.
- `CHANGELOG.md`: added an Unreleased entry.
- `.env.example`: unchanged; there is no implemented cache setting to configure.

Cached token usage was already mapped by `ModelGateway._extract_usage` (`model_gateway.py:355`) and used by the cost calculator (`cost_tracker.py:124`); no change was needed. Provider usage metadata therefore records `cached_content_token_count` when returned by the SDK.

## Validation

| Command | Result |
| :-- | :-- |
| `python -m pytest tests/gateway tests/unit tests/integration -q -p no:cacheprovider -x` from `backend/` | **Not run:** the checked-in venv launcher failed immediately: `No Python at '"C:\Users\AnandShukla1\AppData\Local\Programs\Python\Python312\python.exe'`. |
| `ruff check app tests` | Passed: `All checks passed!` |
| `ruff format --check app tests` | Passed: `424 files already formatted` |
| `mypy app` | **Not run:** same missing Python interpreter error from the venv launcher. |

No real Gemini request was made. No cache minimum, cache hit, ownership boundary, permission failure behavior or storage pricing could be verified offline. No dependencies were added, no `.env` file was read, and no commit was created.
