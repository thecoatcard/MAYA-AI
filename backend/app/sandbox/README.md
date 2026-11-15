# Module: Sandbox Runner

- **Status:** Implemented
- **Owner:** MAYA AI Engineering Team
- **Location:** `backend/app/sandbox/`

## Responsibility

The Sandbox Runner module executes untrusted or dynamically generated code and command-line instructions inside an isolated containerized environment without exposing host resources. It enforces Decision D14: strictly prohibiting Docker socket exposure, executing under a non-root user (UID 10001), enforcing CPU/memory caps, applying execution timeouts, and bounding captured output.

## Interfaces

```python
class DockerSandboxRunner:
    def __init__(self, config: SandboxConfig | None = None) -> None: ...
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
    async def cleanup(self, workspace_path: str) -> None: ...
```

### Key Request & Result Schemas

- `ExecutionRequest`: `command`, `workspace_path`, `timeout_seconds`, `network_enabled`, `env_vars`.
- `ExecutionResult`: `exit_code`, `stdout`, `stderr`, `duration_seconds`, `truncated`, `error_type`.

## Dependencies

- Depends on: Local Docker daemon or container runtime (via CLI), `backend/app/sandbox/schemas.py`.
- Depended on by: `CodingAgent` (`backend/app/agents/specialized/coding.py`).

## Configuration

Standard configuration in `SandboxConfig`:

- `docker_image`: `maya-sandbox:latest` (or `python:3.12-slim`).
- `cpu_limit`: `1.0`.
- `memory_limit_mb`: `512`.
- `default_timeout_seconds`: `60.0`.
- `max_output_bytes`: `1048576` (1 MB).
- `default_network`: `False` (`--network none`).

## Failure Behavior

- **Timeout:** Subprocess is killed immediately after `timeout_seconds` and returns exit code `124`.
- **Output Overflow:** Output exceeding 1 MB is truncated with `truncated = True`.
- **Security Violations:** Any attempt to mount `/var/run/docker.sock` or `//./pipe/docker_engine` raises `SecurityViolationError` before container startup.

## Security Considerations

1. **Docker Socket Ban (Decision D14):** Sockets (`/var/run/docker.sock`, `docker_engine`) are strictly rejected.
2. **Non-Root Execution:** Runs under UID `10001:10001`.
3. **Network Isolation:** Defaults to `--network none` to prevent exfiltration.
4. **Workspace Confinement:** All volume mounts are confined to dedicated ephemeral directories and deleted in `finally` blocks.

## Testing

Covered in `backend/tests/integration/test_sandbox_runner.py`:

- Docker socket prohibition check.
- Non-root UID enforcement.
- Timeout kill (exit code 124).
- 1MB output truncation.
- Workspace filesystem isolation.
