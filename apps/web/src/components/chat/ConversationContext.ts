"use client";

import { createContext, useContext } from "react";

import type { UseAttachmentsResult } from "../../hooks/useAttachments";
import type { UseChatStreamResult } from "../../hooks/useChatStream";
import type { UseLiveVoiceResult, VoiceScope } from "../../hooks/useLiveVoice";
import type { SessionSummary } from "../../lib/api-client";
import type { ThreadItem } from "./threadItems";

/** Public shape of the conversation runtime (one chat + one voice session). */
export interface ConversationRuntimeValue {
  readonly chat: UseChatStreamResult;
  readonly voice: UseLiveVoiceResult;
  /** True while a live session is open (listening, thinking, speaking, interrupted). */
  readonly voiceLive: boolean;
  /** The one ordered thread. */
  readonly items: readonly ThreadItem[];
  /** Which session is live: "master" (top bar, all conversations), "conversation", or null. */
  readonly voiceScope?: VoiceScope | null;
  /**
   * Composer mic: starts the conversation-bound voice, or stops it. While the master voice is on
   * it does nothing (master wins: one audio session).
   */
  readonly toggleVoice: () => void;
  /** Top-bar voice: starts the master tracker, replaces a conversation call, or stops it. */
  readonly toggleMasterVoice?: () => void;
  /** Starts a master voice session that never opens the microphone (scheduled-update reading). */
  readonly startListenOnlyMaster?: () => void;
  /** Restarts the master voice with the microphone open (the Unmute button of that notice). */
  readonly unmuteMaster?: () => void;
  /** Ends the live session whichever scope started it. */
  readonly stopVoice?: () => void;
  /** Live: over the voice socket (same session). Otherwise: REST SSE chat. */
  readonly sendText: (
    text: string,
    options?: { mode?: "deep_research"; sourceMessageId?: string },
  ) => Promise<void>;
  /** Submit typed text to the task engine (POST /api/v1/tasks). */
  readonly runAsTask: (text: string) => Promise<void>;
  /** Add a task card to the thread (deduped by id), for example the new task made by "Try again". */
  readonly trackTask: (taskId: string, title?: string) => void;
  /**
   * A tracked task reached a final state: fetch the "what the task got" message the server posts
   * into the conversation and show it live (no reload needed).
   */
  readonly taskFinished?: (taskId: string) => void;
  readonly newConversation: () => void;
  readonly openConversation: (sessionId: string) => Promise<void>;
  /** `alsoDeleteContent`: also delete the finished tasks and files made in it (default off). */
  readonly deleteConversation: (
    sessionId: string,
    alsoDeleteContent?: boolean,
  ) => Promise<void>;
  /** Conversation actions of the context menus. Each resolves false when it did not apply. */
  readonly conversationActions?: ConversationActions;
  /** The composer registers itself so menus can fill it and move focus there. */
  readonly registerComposer?: (handle: ComposerHandle | null) => void;
  readonly prefillComposer?: (
    text: string,
    options?: { append?: boolean },
  ) => void;
  readonly sessions: {
    readonly list: readonly SessionSummary[];
    /** Archived conversations are listed too (the sidebar "Show archived" switch). */
    readonly showArchived?: boolean;
    readonly setShowArchived?: (show: boolean) => void;
    readonly loading: boolean;
    /** True once the first load has finished (success or failure): before that, no empty state. */
    readonly loaded?: boolean;
    readonly error: string | null;
    readonly reload: () => Promise<void>;
  };
  readonly historyLoading: boolean;
  readonly historyError: string | null;
  /** Refreshes the pending-approval counter (approvals are decided on the task cards). */
  readonly refreshApprovals: () => Promise<void>;
  /**
   * Files attached to the next message (uploaded on select). Optional so a runtime without file
   * support simply shows no attach control.
   */
  readonly attachments?: UseAttachmentsResult;
  /** Plain text for the status bar and the title bar. */
  readonly title: string;
}

/** What the composer offers to the menus. */
export interface ComposerHandle {
  readonly setDraft: (text: string) => void;
  readonly getDraft: () => string;
  readonly focus: () => void;
}

/**
 * Server-backed conversation actions. A method that the server does not have yet (404 or 501)
 * is reported in `unsupported` so the menu hides the action instead of failing.
 */
export interface ConversationActions {
  readonly rename: (id: string, title: string) => Promise<boolean>;
  readonly setPinned: (id: string, pinned: boolean) => Promise<boolean>;
  readonly setArchived: (id: string, archived: boolean) => Promise<boolean>;
  /** Branch a conversation (optionally up to a message) and open the copy. */
  readonly duplicate: (id: string, fromMessageId?: string) => Promise<boolean>;
  /** Downloads the conversation as a Markdown file. */
  readonly exportMarkdown: (id: string, title: string) => Promise<boolean>;
  /** Names of actions the server answered with 404 or 501: hide them. */
  readonly unsupported: ReadonlySet<string>;
  /** Rate a reply. Resolves false when unsupported or failed. */
  readonly feedback: (
    messageId: string,
    value: "up" | "down" | null,
  ) => Promise<boolean>;
}

export const ConversationContext =
  createContext<ConversationRuntimeValue | null>(null);

export function useConversationRuntime(): ConversationRuntimeValue {
  const value = useContext(ConversationContext);
  if (value === null) {
    throw new Error(
      "useConversationRuntime must be used inside ConversationRuntime",
    );
  }
  return value;
}

/**
 * A stable ref to the latest runtime value. Menus read it when they open, so a message does not
 * re-render on every streamed token just because the runtime value changed.
 */
export const ConversationRefContext = createContext<{
  readonly current: ConversationRuntimeValue | null;
} | null>(null);

const NO_RUNTIME: { readonly current: ConversationRuntimeValue | null } = {
  current: null,
};

/** The latest runtime, read lazily (null outside the runtime, for example in isolated tests). */
export function useConversationRef(): {
  readonly current: ConversationRuntimeValue | null;
} {
  return useContext(ConversationRefContext) ?? NO_RUNTIME;
}
