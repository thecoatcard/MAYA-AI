# runtime_tools

Model-written Python tools: MAYA can search for an existing tool, propose a new one, and run an
approved one. **Status: partly implemented, default off, never run against a real container.**
Design record: [ADR-0027](../../../docs/adr/0027-runtime-tools.md).

## Safety stance

- Code runs **only** in an isolated container (`executor.py`), never on the host. The existing
  `app.sandbox.SandboxRunner` is not used for this: it runs `subprocess.run` with the host's Python
  and only scrubs the environment, so it gives no isolation.
- No sandbox image configured (`MAYA_RUNTIME_TOOLS_SANDBOX_IMAGE` empty, the default) means every
  run is refused with a plain message. There is no fallback to the host.
- `MAYA_RUNTIME_TOOLS_ENABLED` is `false` by default; while it is off the tools are not offered to
  the model at all.
- A new tool is shown to the user in full and runs only after approval, through the same inline
  approve/reject card as other guarded actions (`runtime_tool_install` is `APPROVAL_REQUIRED`).
  A typed or spoken "yes" never approves.
- `static_check.py` rejects banned imports and dynamic code before a tool is even proposed. This is
  defence in depth, the container is the real boundary.
- Tool output is untrusted data and is wrapped as such before the model sees it.

## Modules

| File              | Purpose                                                                                |
| :---------------- | :------------------------------------------------------------------------------------- |
| `static_check.py` | `ast` allowlist check of a tool's source                                               |
| `schema_check.py` | Validates a tool's declared input schema                                               |
| `registry.py`     | File-based per-workspace registry of tools and versions (no database migration)        |
| `executor.py`     | `ToolExecutor` interface, hardened `docker run` builder, `UnavailableExecutor`         |
| `service.py`      | Search, propose, approve and run, with per-turn run budget and output/file caps        |
| `chat.py`         | Chat tool definitions: `runtime_tool_search`, `runtime_tool_propose`, `runtime_tool_run` |
| `factory.py`      | Builds the service from settings, or `None` when disabled                              |

## What is verified

- Unit tests cover the registry and the static check (`tests/unit/test_runtime_tools_*.py`).

## What is not verified

- The container executor, the service and the chat wiring have no dedicated tests yet.
- No tool has ever been run in a real container; the isolation flags are checked by reading the
  command line only.
- Do not enable `MAYA_RUNTIME_TOOLS_ENABLED` or set an image until those tests exist and one tool
  has been run in a real sandbox and reviewed by the owner.
