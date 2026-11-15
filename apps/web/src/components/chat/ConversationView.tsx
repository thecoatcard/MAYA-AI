"use client";

import { TaskUpdatesBlock } from "../voice/TaskUpdatesBlock";
import { Brain, ListTodo, Paperclip, X } from "lucide-react";
import React, { useCallback, useEffect, useRef, useState } from "react";

import { useFileDrop } from "../../hooks/useFileDrop";
import { useContextMenu } from "../ui/ContextMenu";
import { WorkingFolderStatus } from "../files/WorkingFolderStatus";
import { Button } from "../ui/Button";
import { VoiceToggle } from "../voice/VoiceToggle";
import { MUTED_NOTICE } from "../voice/voice-copy";
import { answerReadableText } from "../../lib/ask-user";
import { ChatInput } from "./ChatInput";
import { ChatNotice } from "./ChatNotice";
import { describeVoiceUnavailable } from "../../lib/voice-reasons";
import { useConversationRuntime } from "./ConversationContext";
import { ScheduleDraftCard } from "./ScheduleDraftCard";
import { ScheduledUpdatesNotice } from "./ScheduledUpdatesNotice";
import { ThreadList } from "./ThreadList";
import {
  latestProgressLabel,
  runningStepLabel,
} from "../../lib/progress-steps";

import "./chat.css";
import "./EmptyState.css";
import { LivingBackdrop } from "./LivingBackdrop";

/** Starter prompts. Choosing one only fills the draft; the user edits and sends it. */
export const EMPTY_SUGGESTIONS = [
  { label: "Make a 10-page PDF", draft: "Make a 10-page PDF about " },
  { label: "Research and summarise", draft: "Research and summarise " },
  { label: "Write notes in Notion", draft: "Write notes in Notion about " },
  { label: "Check my mail", draft: "Check my mail for " },
] as const;

/**
 * `calm` is true once typing or voice has started: the living backdrop is then unmounted
 * (its animation loop is cancelled), while the text and suggestions stay put.
 */
export function EmptyConversation({
  calm,
  onPick,
}: {
  calm: boolean;
  onPick: (draft: string) => void;
}): React.JSX.Element {
  return (
    <section
      className="maya-chat-empty maya-empty motion-stagger"
      aria-labelledby="maya-empty-title"
    >
      <div className="maya-empty__mark" aria-hidden="true">
        {calm ? null : <LivingBackdrop />}
      </div>
      <h2 id="maya-empty-title" className="maya-chat-empty__title">
        Start a conversation
      </h2>
      <p className="maya-chat-empty__body maya-empty__hint">
        Type a message, or use the microphone to talk. Typed and spoken turns
        stay in this one thread.
      </p>
      <ul className="maya-empty__chips motion-stagger" aria-label="Suggestions">
        {EMPTY_SUGGESTIONS.map((s) => (
          <li key={s.label}>
            <button
              type="button"
              className="maya-empty__chip motion-interactive motion-press motion-lift"
              onClick={() => onPick(s.draft)}
            >
              {s.label}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The unified conversation: one thread, one composer. Text and voice share the
 * same session; tasks and approvals appear inline as they happen.
 */
export function ConversationView(): React.JSX.Element {
  const runtime = useConversationRuntime();
  const { chat, voice, voiceLive } = runtime;
  const [draft, setDraft] = useState("");
  // Deep research is per message: off by default and off again once the message is sent.
  const [deepResearch, setDeepResearch] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const files = runtime.attachments;
  // Menus fill the composer through this handle (Edit and resend, Quote in reply).
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const registerComposer = runtime.registerComposer;
  useEffect(() => {
    registerComposer?.({
      setDraft,
      getDraft: () => draftRef.current,
      focus: () => {
        window.setTimeout(() => {
          const box = inputRef.current;
          if (box === null) return;
          box.focus();
          box.setSelectionRange(box.value.length, box.value.length);
        }, 0);
      },
    });
    return () => registerComposer?.(null);
  }, [registerComposer]);
  const researchMenu = useContextMenu({
    label: "Deep research actions",
    getItems: () => [
      {
        id: "research",
        label: deepResearch
          ? "Turn deep research off"
          : "Turn deep research on",
        icon: <Brain size={14} />,
        disabled: voiceLive || chat.isBusy,
        disabledReason: voiceLive
          ? "Not available during a live voice session."
          : "Wait for the reply to finish.",
        onSelect: () => setDeepResearch((on) => !on),
      },
    ],
  });
  const attachOffReason = voiceLive
    ? "Files cannot be attached during a live voice session. End the voice session first."
    : null;
  const addFiles = files?.addFiles;
  const { dragging, bindings: dropBindings } = useFileDrop(
    useCallback((dropped: File[]) => addFiles?.(dropped), [addFiles]),
    files !== undefined && attachOffReason === null,
  );

  // Esc interrupts MAYA's speech (barge-in) wherever focus is.
  const speaking = voice.state === "speaking";
  const interrupt = voice.interruptPlayback;
  useEffect(() => {
    if (!speaking) return undefined;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [speaking, interrupt]);

  const submit = useCallback(
    (text: string): void => {
      setDraft("");
      if (deepResearch && !voiceLive) {
        setDeepResearch(false);
        void runtime.sendText(text, { mode: "deep_research" });
        return;
      }
      void runtime.sendText(text);
    },
    [runtime, deepResearch, voiceLive],
  );

  const runAsTask = (): void => {
    const text = draft.trim();
    if (text === "") return;
    setDraft("");
    void runtime.runAsTask(text);
  };

  const voiceOpen = voice.state !== "idle";
  // The newest step of the reply in progress: one status line, not a list.
  const streamingReply = [...(chat.messages ?? [])]
    .reverse()
    .find((m) => m.role === "assistant" && m.state === "streaming");
  // With no reply streaming, the running step of a background task of this voice session.
  const voiceProgressLine =
    latestProgressLabel(streamingReply?.progress) ??
    runningStepLabel(voice.lastTaskEvent);
  // A task update MAYA spoke by herself (done, failed, needs approval or a detail, or the
  // catch-up after voice was off). It is a status entry and needs no chat message.
  const spokenUpdate = voice.lastSpokenUpdate ?? null;
  const detail =
    voice.state === "unavailable"
      ? describeVoiceUnavailable(voice.unavailableReason)
      : voice.state === "error"
        ? (voice.error?.message ?? "Voice failed. Text chat still works.")
        : null;
  const running =
    voice.state !== "idle" &&
    voice.state !== "unavailable" &&
    voice.state !== "error";
  const muted =
    running && voice.state !== "connecting" && (voice.muted ?? false);

  return (
    <div
      className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col"
      data-testid="conversation"
      {...dropBindings}
    >
      {dragging && (
        <div className="maya-dropzone" data-testid="drop-overlay">
          <p className="maya-dropzone__label">
            <Paperclip aria-hidden="true" size={18} />
            Drop files to attach them
          </p>
        </div>
      )}
      {dragging && (
        <p role="status" className="maya-sr-only">
          Drop files to attach them to your next message.
        </p>
      )}
      <div className="flex min-w-0 shrink-0 items-center gap-3 border-b border-maya-border px-4 py-2">
        <h1
          dir="auto"
          title={runtime.title}
          className="m-0 min-w-0 flex-1 truncate text-maya-heading font-medium text-maya-text"
        >
          {runtime.title}
        </h1>
        <span
          className="shrink-0 text-maya-caption text-maya-muted"
          data-testid="mode-label"
        >
          {voiceLive ? "Text and voice live" : "Text"}
        </span>
      </div>

      {runtime.historyError !== null && (
        <p
          role="alert"
          className="m-0 px-4 py-2 text-maya-small text-maya-danger [overflow-wrap:anywhere]"
        >
          {runtime.historyError}
        </p>
      )}
      {runtime.historyLoading && (
        <p
          role="status"
          className="m-0 px-4 py-2 text-maya-small text-maya-muted"
        >
          Loading conversation
        </p>
      )}

      <ScheduledUpdatesNotice />

      <ThreadList
        items={runtime.items}
        status={chat.status}
        empty={
          <EmptyConversation
            calm={voiceLive || draft !== ""}
            onPick={(text) => {
              setDraft(text);
              window.setTimeout(() => {
                const box = inputRef.current;
                if (box === null) return;
                box.focus();
                box.setSelectionRange(text.length, text.length);
              }, 0);
            }}
          />
        }
        onAskAgain={chat.canAskAgain ? () => void chat.askAgain() : undefined}
        onTaskRetried={(taskId) => runtime.trackTask(taskId)}
        onTaskAborted={() => void runtime.refreshApprovals()}
        onTaskFinished={(taskId) => runtime.taskFinished?.(taskId)}
        onApprovalDecided={() => void runtime.refreshApprovals()}
        onAnswerInput={(request, answers) => {
          // The answer goes through the chat message path with the readable text; it is the
          // user's reply, never an approval.
          void chat.send(answerReadableText(request.questions, answers), {
            answer: {
              askId: request.askId,
              answers: answers.map((a, i) => ({
                question: request.questions[i]?.question ?? "",
                selected: a.selected,
                text: a.text,
              })),
            },
          });
        }}
        onSkipInput={() => inputRef.current?.focus()}
        onTaskDetail={(_taskId, title, question, answer) =>
          void runtime.sendText(
            `About the task "${title}" (${question}): ${answer}. Please start it again with this detail.`,
          )
        }
      />

      <div className="maya-chat__dock">
        <ChatNotice
          error={chat.error}
          retrying={chat.retrying}
          canRetry={chat.canRetry}
          canAskAgain={chat.canAskAgain}
          onRetry={() => void chat.retry()}
          onAskAgain={() => void chat.askAgain()}
          onDismiss={() => {
            const restored = chat.dismissError();
            if (restored !== null) setDraft(restored);
          }}
        />
        {detail !== null && (
          <p
            role="alert"
            className="m-0 text-maya-small text-maya-danger [overflow-wrap:anywhere]"
            data-testid="voice-detail"
          >
            {detail}
          </p>
        )}
        {muted && (
          <p
            className="m-0 text-maya-small text-maya-secondary"
            data-testid="voice-muted-notice"
          >
            {MUTED_NOTICE}
          </p>
        )}
        {voiceOpen &&
          (voice.taskUpdates ?? []).map((block) => (
            <TaskUpdatesBlock key={block.taskId ?? "catch-up"} block={block} />
          ))}
        {voiceOpen &&
          spokenUpdate !== null &&
          (voice.taskUpdates ?? []).length === 0 && (
            <p
              role="status"
              className="m-0 min-w-0 truncate text-maya-small text-maya-secondary"
              data-testid="voice-update-line"
            >
              {spokenUpdate.catchUp ? "" : "MAYA: "}
              {spokenUpdate.text}
            </p>
          )}
        {voiceOpen && voiceProgressLine !== null && (
          <p
            role="status"
            className="m-0 min-w-0 truncate text-maya-small text-maya-muted"
            data-testid="voice-progress-line"
          >
            {voiceProgressLine}
          </p>
        )}
        {(voice.scheduleDrafts ?? []).map((d) => (
          <ScheduleDraftCard key={d.draftId} draftId={d.draftId} />
        ))}
        <div className="maya-chat__folder-status">
          <WorkingFolderStatus />
        </div>
        {deepResearch && !voiceLive && (
          <div className="maya-composer__mode" data-testid="deep-research-chip">
            <span className="maya-chip maya-chip--accent">
              <Brain aria-hidden="true" size={12} />
              Deep research
              <button
                type="button"
                className="maya-composer__mode-x motion-interactive"
                aria-label="Turn off deep research"
                onClick={() => setDeepResearch(false)}
              >
                <X aria-hidden="true" size={12} />
              </button>
            </span>
            <span className="maya-composer__mode-hint">
              Reads 20+ sites, takes a few minutes
            </span>
          </div>
        )}
        <ChatInput
          value={draft}
          onValueChange={setDraft}
          onSubmit={submit}
          onStop={chat.stop}
          busy={chat.isBusy}
          inputRef={inputRef}
          attachments={files?.items}
          onAttachFiles={files?.addFiles}
          onRemoveAttachment={files?.remove}
          attachDisabledReason={attachOffReason}
          attachNotice={files?.notice ?? null}
          onDismissAttachNotice={files?.dismissNotice}
          hint={
            speaking
              ? "MAYA is speaking. Press Esc to interrupt."
              : running
                ? "Voice is on. Click the waveform to stop. Enter to send typed text."
                : "Enter to send. Shift+Enter for a new line."
          }
          leading={<VoiceToggle />}
          extraActions={
            <>
              <button
                type="button"
                onClick={() => setDeepResearch((on) => !on)}
                aria-label="Deep research"
                aria-pressed={deepResearch}
                title={
                  voiceLive
                    ? "Deep research is not available during a live voice session."
                    : "Deep research"
                }
                disabled={chat.isBusy || voiceLive}
                {...researchMenu.triggerProps}
                data-testid="deep-research-toggle"
                data-on={deepResearch && !voiceLive ? "true" : "false"}
                className="maya-composer__mic maya-composer__research motion-interactive"
              >
                <Brain aria-hidden="true" size={16} />
              </button>
              {researchMenu.menu}
              <Button
                onClick={runAsTask}
                disabled={draft.trim() === "" || (files?.count ?? 0) > 0}
                title={
                  (files?.count ?? 0) > 0
                    ? "Attached files go with a chat message. Remove them to run this text as a task."
                    : "Sends this text to the task engine instead of the chat. Chat cannot create tasks by itself yet."
                }
                leftIcon={<ListTodo size={14} />}
                className="maya-composer__task"
              >
                Run as task
              </Button>
            </>
          }
        />
      </div>
    </div>
  );
}
