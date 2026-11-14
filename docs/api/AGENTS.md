# Agents API (`/api/v1/agents`)

Implemented by `backend/app/api/v1/agents.py` (ADR-0023). Bearer-authenticated, scoped to the caller, `Cache-Control: no-store`.

| Route                                       | Purpose                                                                                                                                                                                                                     |
| :------------------------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/agents`                        | The three built-in agents (research, builder, operator) with capabilities and availability, the custom agents found in the connected folder (valid, or invalid with a reason), whether a folder is connected, and the capability vocabulary with what the user may use. |
| `POST /api/v1/agents/custom/preview`        | Validate a definition and return the exact file text and path. Writes nothing.                                                                                                                                              |
| `POST /api/v1/agents/custom/draft`          | `{request}`: MAYA drafts a definition with the model. Capabilities outside the vocabulary or the user's permissions are dropped. Returns a preview; writes nothing.                                                          |
| `POST /api/v1/agents/custom`                | Create `.maya/agents/<name>.md`. Requires `confirm: true` (else 428). 409 if it exists.                                                                                                                                     |
| `PUT /api/v1/agents/custom/{name}`          | Replace an existing agent (previous text kept as `.md.bak`). Requires `confirm: true`.                                                                                                                                      |
| `POST /api/v1/agents/custom/{name}/enabled` | `{enabled}`: enable or disable (an `enabled` edit; nothing is deleted).                                                                                                                                                     |

Body fields: `name`, `description`, `instructions`, `capabilities` (`web_search`, `read_files`), optional `model` (`lite`, `orchestrator`, `heavy`), `enabled`, `confirm`. Unknown fields are rejected (422). Errors are `{error, message}` with codes such as `no_folder` (409), `unknown_capability`, `capability_not_permitted`, `bad_name`, `reserved_name` and `unknown_key` (422).
