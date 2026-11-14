"use client";

import React, { useId, useState } from "react";

import type { AgentDraft } from "../../hooks/useChatStream";
import { ApiError } from "../../lib/api-client";
import {
  createAgent,
  describeAgentsError,
  previewAgent,
  type AgentDraftInput,
} from "../../lib/agents-client";
import { Button, Notice, WrapText } from "../ui";

type CardState = "review" | "editing" | "saving" | "created" | "cancelled";

export interface AgentDraftCardProps {
  readonly draft: AgentDraft;
}

/**
 * A custom agent MAYA drafted in chat: the exact file, with Approve, Edit and Cancel. Nothing is
 * written to the working folder until Approve (the server also refuses a write without
 * `confirm`). Edit re-previews the changed text before it can be approved. The state is always
 * stated in words.
 */
export function AgentDraftCard({
  draft,
}: AgentDraftCardProps): React.JSX.Element {
  const ids = useId();
  const [state, setState] = useState<CardState>("review");
  const [input, setInput] = useState<AgentDraftInput>({
    name: draft.name,
    description: draft.description,
    instructions: draft.instructions,
    capabilities: draft.capabilities,
  });
  const [shown, setShown] = useState({
    path: draft.path,
    content: draft.content,
  });
  const [error, setError] = useState<string | null>(null);

  const approve = async (): Promise<void> => {
    setState("saving");
    setError(null);
    try {
      await createAgent(input);
      setState("created");
    } catch (e) {
      setState("review");
      setError(
        e instanceof ApiError && e.status === 409 && !e.serverMessage
          ? "An agent with this name already exists."
          : describeAgentsError(e),
      );
    }
  };

  const repreview = async (): Promise<void> => {
    setError(null);
    try {
      const result = await previewAgent(input);
      setShown({ path: result.path, content: result.content });
      setState("review");
    } catch (e) {
      setError(describeAgentsError(e));
    }
  };

  if (state === "created") {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-text-muted">
        Agent <strong>{input.name}</strong> created in <code>{shown.path}</code>
        . Use it by name, for example @{input.name}.
      </p>
    );
  }
  if (state === "cancelled") {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-text-muted">
        Draft of agent {input.name} discarded. Nothing was saved.
      </p>
    );
  }

  const busy = state === "saving";
  return (
    <section
      aria-label={`Draft agent ${input.name}`}
      className="flex min-w-0 flex-col gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3 shadow-maya-1 motion-none"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="m-0 font-medium text-maya-text">
          Draft agent: {input.name}
        </p>
        <span className="maya-chip font-mono text-maya-caption text-maya-muted">
          {state}
        </span>
      </div>
      <WrapText>{input.description}</WrapText>
      {state === "editing" ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-d`} className="text-maya-small">
            Description
          </label>
          <input
            id={`${ids}-d`}
            className="rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
            value={input.description}
            onChange={(e) =>
              setInput({ ...input, description: e.target.value })
            }
          />
          <label htmlFor={`${ids}-i`} className="text-maya-small">
            Instructions
          </label>
          <textarea
            id={`${ids}-i`}
            className="min-h-32 rounded-maya border border-maya-border bg-maya-surface p-2 text-maya-text"
            value={input.instructions}
            onChange={(e) =>
              setInput({ ...input, instructions: e.target.value })
            }
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void repreview()}>Preview changes</Button>
            <Button variant="secondary" onClick={() => setState("review")}>
              Back
            </Button>
          </div>
        </div>
      ) : (
        <>
          <p className="m-0 text-maya-small">
            This file will be written to <code>{shown.path}</code>:
          </p>
          <pre className="m-0 max-h-64 overflow-auto whitespace-pre-wrap rounded-maya border border-maya-border p-2 text-maya-small">
            {shown.content}
          </pre>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => void approve()}>
              Approve
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setState("editing")}
            >
              Edit
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setState("cancelled")}
            >
              Cancel
            </Button>
          </div>
        </>
      )}
      {error !== null && (
        <Notice tone="danger" onDismiss={() => setError(null)}>
          <WrapText>{error}</WrapText>
        </Notice>
      )}
    </section>
  );
}
