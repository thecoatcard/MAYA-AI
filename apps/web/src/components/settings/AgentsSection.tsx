"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import {
  createAgent,
  describeAgentsError,
  draftAgent,
  getAgents,
  previewAgent,
  setAgentEnabled,
  updateAgent,
  type AgentDraftInput,
  type AgentPreview,
  type AgentsOverview,
  type CustomAgent,
} from "../../lib/agents-client";
import { Badge, Button, ErrorState, Notice, Skeleton, WrapText } from "../ui";

const EMPTY: AgentDraftInput = {
  name: "",
  description: "",
  instructions: "",
  capabilities: [],
};

interface Editing {
  readonly input: AgentDraftInput;
  /** True when changing an existing agent (its name cannot change). */
  readonly existing: boolean;
}

export function AgentsSection(): React.JSX.Element {
  const [overview, setOverview] = useState<AgentsOverview | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [preview, setPreview] = useState<AgentPreview | null>(null);
  const [sentence, setSentence] = useState("");
  const ids = useId();

  const load = useCallback(async () => {
    try {
      setOverview(await getAgents());
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<void>): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(describeAgentsError(e));
    } finally {
      setBusy(false);
    }
  };

  if (loadFailed) {
    return (
      <ErrorState
        title="Agents could not be loaded"
        onRetry={() => void load()}
      />
    );
  }
  if (overview === null) {
    return <Skeleton aria-label="Loading agents" />;
  }

  const allowed = Object.entries(overview.capabilities).filter(
    ([, info]) => info.allowed,
  );

  const toggle = (agent: CustomAgent): Promise<void> =>
    run(async () => {
      await setAgentEnabled(agent.name, !agent.enabled);
      await load();
    });

  const draft = (): Promise<void> =>
    run(async () => {
      const result = await draftAgent(sentence);
      setPreview(result);
      setEditing({
        existing: false,
        input: {
          name: result.agent.name,
          description: result.agent.description,
          instructions: result.agent.instructions,
          capabilities: result.agent.capabilities,
        },
      });
    });

  const showPreview = (): Promise<void> =>
    run(async () => {
      if (editing) setPreview(await previewAgent(editing.input));
    });

  const save = (): Promise<void> =>
    run(async () => {
      if (!editing) return;
      if (editing.existing) await updateAgent(editing.input);
      else await createAgent(editing.input);
      setEditing(null);
      setPreview(null);
      setSentence("");
      await load();
    });

  const patch = (change: Partial<AgentDraftInput>): void => {
    setPreview(null);
    setEditing((current) =>
      current
        ? { ...current, input: { ...current.input, ...change } }
        : current,
    );
  };

  return (
    <div className="flex min-w-0 flex-col gap-4 py-4">
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {overview.agents.map((agent) => (
          <li key={agent.id} className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-maya-text">{agent.label}</span>
              <Badge tone={agent.available ? "success" : "warning"}>
                {agent.available ? "Ready" : "Not connected"}
              </Badge>
            </div>
            <WrapText>{agent.summary}</WrapText>
            <p className="m-0 text-maya-small text-maya-text-muted">
              {agent.capabilities.join(" · ")}
            </p>
          </li>
        ))}
      </ul>

      <div className="flex min-w-0 flex-col gap-3 border-t border-maya-border pt-4">
        <h3 className="m-0 text-maya-body font-semibold text-maya-text">
          Custom agents
        </h3>
        {!overview.folderConnected ? (
          <Notice tone="info">
            Connect a working folder (Files) to create custom agents. They are
            saved as files in <code>.maya/agents</code> inside that folder.
          </Notice>
        ) : (
          <>
            {overview.custom.length === 0 && (
              <p className="m-0 text-maya-text-muted">
                No custom agents yet. Describe one below, or say &quot;create an
                agent for ...&quot; and review it here.
              </p>
            )}
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {overview.custom.map((agent) => (
                <li key={agent.file} className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-maya-text">
                      {agent.name}
                    </span>
                    {agent.valid ? (
                      <Badge tone={agent.enabled ? "success" : "neutral"}>
                        {agent.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    ) : (
                      <Badge tone="danger">Not usable</Badge>
                    )}
                  </div>
                  {agent.valid ? (
                    <>
                      <WrapText>{agent.description}</WrapText>
                      <p className="m-0 text-maya-small text-maya-text-muted">
                        {agent.capabilities.length > 0
                          ? agent.capabilities.join(" · ")
                          : "Text only"}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          disabled={busy}
                          onClick={() => void toggle(agent)}
                        >
                          {agent.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          variant="secondary"
                          disabled={busy}
                          onClick={() => {
                            setPreview(null);
                            setEditing({
                              existing: true,
                              input: {
                                name: agent.name,
                                description: agent.description,
                                instructions: agent.instructions,
                                capabilities: agent.capabilities,
                              },
                            });
                          }}
                        >
                          Edit
                        </Button>
                      </div>
                    </>
                  ) : (
                    <WrapText>
                      {agent.error ?? "This file cannot be used."}
                    </WrapText>
                  )}
                </li>
              ))}
            </ul>

            {editing === null && (
              <div className="flex flex-col gap-2">
                <label htmlFor={`${ids}-sentence`} className="text-maya-small">
                  What should the agent do?
                </label>
                <textarea
                  id={`${ids}-sentence`}
                  className="min-h-16 w-full rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
                  value={sentence}
                  onChange={(e) => setSentence(e.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={busy || sentence.trim().length < 5}
                    onClick={() => void draft()}
                  >
                    Draft with MAYA
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      setEditing({ existing: false, input: EMPTY })
                    }
                  >
                    Write it myself
                  </Button>
                </div>
              </div>
            )}

            {editing !== null && (
              <form
                className="flex flex-col gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (preview === null) {
                    void showPreview();
                  } else {
                    void save();
                  }
                }}
              >
                <label htmlFor={`${ids}-name`} className="text-maya-small">
                  Name (lowercase letters, digits and dashes)
                </label>
                <input
                  id={`${ids}-name`}
                  className="rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
                  value={editing.input.name}
                  readOnly={editing.existing}
                  onChange={(e) => patch({ name: e.target.value })}
                />
                <label htmlFor={`${ids}-desc`} className="text-maya-small">
                  Description
                </label>
                <input
                  id={`${ids}-desc`}
                  className="rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
                  value={editing.input.description}
                  onChange={(e) => patch({ description: e.target.value })}
                />
                <label htmlFor={`${ids}-instr`} className="text-maya-small">
                  Instructions
                </label>
                <textarea
                  id={`${ids}-instr`}
                  className="min-h-32 rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
                  value={editing.input.instructions}
                  onChange={(e) => patch({ instructions: e.target.value })}
                />
                <fieldset className="m-0 border-0 p-0">
                  <legend className="text-maya-small">
                    Capabilities (a custom agent can only use what you already
                    have, read-only)
                  </legend>
                  {allowed.map(([name, info]) => (
                    <label key={name} className="mr-4 inline-flex gap-2">
                      <input
                        type="checkbox"
                        checked={editing.input.capabilities.includes(name)}
                        onChange={(e) =>
                          patch({
                            capabilities: e.target.checked
                              ? [...editing.input.capabilities, name]
                              : editing.input.capabilities.filter(
                                  (c) => c !== name,
                                ),
                          })
                        }
                      />
                      {name} ({info.tools.join(", ")})
                    </label>
                  ))}
                </fieldset>
                {preview !== null && (
                  <div>
                    <p className="m-0 text-maya-small">
                      This file will be written to <code>{preview.path}</code>:
                    </p>
                    <pre className="m-0 max-h-64 overflow-auto whitespace-pre-wrap rounded-maya border border-maya-border p-2 text-maya-small">
                      {preview.content}
                    </pre>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  {preview === null ? (
                    <Button type="submit" disabled={busy}>
                      Preview file
                    </Button>
                  ) : (
                    <Button type="submit" disabled={busy}>
                      {editing.existing
                        ? "Approve and save"
                        : "Approve and create"}
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      setEditing(null);
                      setPreview(null);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            )}
          </>
        )}
        {error !== null && (
          <Notice tone="danger" onDismiss={() => setError(null)}>
            <WrapText>{error}</WrapText>
          </Notice>
        )}
      </div>
    </div>
  );
}
