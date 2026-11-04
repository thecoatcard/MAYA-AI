# Agent: <agent name>

- **Status:** Implemented | Partially implemented | Planned
- **Model role:** <configuration alias, not a hard-coded model identifier>

## Responsibility

<What the agent is for and what is out of scope.>

## Tools

| Tool | Source | Permission level |
| :--- | :--- | :--- |
| <tool name> | <server or library> | READ, WRITE, APPROVAL_REQUIRED or RESTRICTED |

The agent can call only the tools listed here.

## Permissions and boundaries

<What it may do autonomously, what needs human approval, what it must never do. Network and filesystem reach.>

## Inputs and outputs

<Typed input and output payloads, and the verification proof required before a step is marked complete.>

## Workflows

<The user-visible workflows that use this agent, with links.>

## Failure handling

<Retries, timeouts, cancellation, checkpointing, compensating actions.>

## Prompt-injection exposure

<Which untrusted content the agent reads and how it is isolated from tools that can change things.>

## Testing

<Unit, integration and evaluation tests, and the datasets used.>
