# HTTP API

Mounted by the application factory under `/api/v1`: [health probes](../../../docs/api/HEALTH.md) and the
[authentication endpoints](../../../docs/api/AUTH.md). Every error is `{"error": "<code>"}` (see `v1/errors.py`),
validation errors name fields but never repeat submitted values, and each request gets a generated correlation ID.
There are no conversation or task routes yet.
