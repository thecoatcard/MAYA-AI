# API: <resource or area>

- **Base path:** <for example /api/v1>
- **Authentication:** <scheme and required scopes>
- **Status:** Implemented | Partially implemented | Planned

## <METHOD> <path>

<One sentence on what the endpoint does.>

**Permissions:** <role, scope, or approval tier required>

**Idempotency:** <whether an idempotency key is accepted or required>

### Request

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| <name> | <type> | <yes or no> | <meaning and constraints> |

Example:

```json
{ "<field>": "<example value>" }
```

### Response

Status codes: <list each code and when it is returned>.

Example:

```json
{ "<field>": "<example value>" }
```

### Errors

| Code | Meaning | Client action |
| :--- | :--- | :--- |
| <status> | <condition> | <what to do> |

### Compatibility

<Breaking or deprecated behavior and the version it changes in.>
