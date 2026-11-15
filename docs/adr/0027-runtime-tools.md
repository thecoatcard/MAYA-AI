# ADR-0027: Runtime tools (model-written Python, container only)

- Status: Proposed
- Date: 2026-11-12

## Context

The owner asked that MAYA be able to build a tool at run time when it lacks a capability, and keep
it for reuse. Running model-written code is the most dangerous capability the product could have.

Reading `backend/app/sandbox/runner.py` showed that the existing `SandboxRunner` does not isolate:
it runs the host's Python through `subprocess.run` and only scrubs the environment. Its name and
README suggest otherwise. Model-written code must not be given to it.

## Decision

1. Code runs only in an isolated container started by a separate `ContainerExecutor`: no network,
   read-only root filesystem, all capabilities dropped, no new privileges, non-root user, memory,
   CPU, process and file-size limits, no environment passed in, and two bind mounts only (the tool
   code read-only, a per-run work folder). The Docker socket is refused.
2. No sandbox image configured means no isolated runtime exists, so every run is refused. There is
   no host fallback.
3. The feature is off by default (`MAYA_RUNTIME_TOOLS_ENABLED=false`) and its tools are not offered
   to the model while off.
4. Every new tool version is shown to the user in full (source, input schema, capabilities) and
   runs only after approval on the inline approve/reject card. The approval is bound to the SHA-256
   of the source. Changed code needs a new approval. Typed or spoken text never approves.
5. Static `ast` checks (allowlisted imports, no dynamic code) run before a proposal. They are
   defence in depth only.
6. Tools are stored as files per workspace (no database migration). Tool output is untrusted data.

## Consequences

- Enabling the feature needs a real sandbox image and Docker on the host. Both are infrastructure
  decisions for the owner.
- The existing `SandboxRunner` README and the earlier "D14 isolation" wording overstate what it
  does; that should be corrected separately.

## Status of the implementation

Implemented: registry, static check, schema check, executor (command builder), service, chat tools.
Tested: registry and static check. Not tested: executor, service, chat wiring. Not run: any tool in
a real container. The owner must review this ADR and run one tool in a real sandbox before turning
the feature on.
