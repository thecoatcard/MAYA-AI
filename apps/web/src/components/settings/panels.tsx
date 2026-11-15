"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import React, { useEffect, useState } from "react";

import packageInfo from "../../../package.json";
import { getFolderPicker } from "../../lib/desktop-bridge";
import type { ChatSettings } from "../../lib/settings-client";
import type { SystemHealth, UserPrincipal } from "../../lib/system-client";
import { useTheme, type Theme } from "../../lib/theme";
import { Badge, Button, ErrorState, Skeleton, WrapText, cx } from "../ui";
import { Fact, SettingRow, SettingsCard, SwitchRow } from "./primitives";

export type SwitchKey = "intent_classifier" | "memory_in_chat";
export type SaveState = "idle" | "saving" | "saved";

export interface ChatSettingsModel {
  readonly settings: ChatSettings | null;
  readonly failed: boolean;
  readonly saveState: { key: SwitchKey | null; state: SaveState };
  readonly onRetry: () => void;
  readonly onToggle: (key: SwitchKey) => void;
}

export interface AccountModel {
  readonly principal: UserPrincipal | null;
  readonly health: SystemHealth | null;
  readonly state: "loading" | "ready" | "error";
  readonly onRetry: () => void;
}

function stateFor(
  model: ChatSettingsModel,
  key: SwitchKey,
  on: boolean,
): string {
  if (model.saveState.key === key && model.saveState.state === "saving") {
    return "Saving…";
  }
  if (model.saveState.key === key && model.saveState.state === "saved") {
    return "Saved";
  }
  return on ? "On" : "Off";
}

function ChatLoading({
  model,
  children,
}: {
  readonly model: ChatSettingsModel;
  readonly children: (settings: ChatSettings) => React.ReactNode;
}): React.JSX.Element {
  if (model.failed) {
    return (
      <ErrorState
        title="Chat settings could not be loaded"
        message="Nothing was changed."
        onRetry={model.onRetry}
      />
    );
  }
  if (model.settings === null) {
    return (
      <div aria-busy="true" aria-label="Loading settings" className="py-2">
        <Skeleton lines={4} />
      </div>
    );
  }
  return <>{children(model.settings)}</>;
}

/* --------------------------------------------------------------------------------- General */

const THEMES: readonly { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function GeneralPanel({
  chat,
}: {
  readonly chat: ChatSettingsModel;
}): React.JSX.Element {
  const { theme, setTheme } = useTheme();
  const busy = chat.saveState.state === "saving";
  return (
    <div className="grid min-w-0 gap-4">
      <SettingsCard title="Appearance">
        <SettingRow
          id="theme"
          title="Theme"
          help="Light or dark. The choice applies at once and is remembered on this device."
        >
          <div
            role="radiogroup"
            aria-labelledby="theme-label"
            aria-describedby="theme-help"
            className="inline-flex overflow-hidden rounded-maya-md border border-maya-border-strong"
          >
            {THEMES.map((option) => (
              <label
                key={option.value}
                className={cx(
                  "motion-interactive relative inline-flex min-h-9 cursor-pointer items-center px-4 text-maya-small pointer-coarse:min-h-11",
                  theme === option.value
                    ? "bg-maya-accent font-medium text-maya-accent-text"
                    : "bg-maya-raised text-maya-text hover:bg-maya-hover",
                  "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-[-2px] has-[:focus-visible]:outline-maya-focus",
                )}
              >
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={theme === option.value}
                  onChange={() => setTheme(option.value)}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
          </div>
        </SettingRow>
      </SettingsCard>
      <SettingsCard
        title="Messages"
        description="Saved as soon as you change them."
      >
        <div className="min-w-0">
          <ChatLoading model={chat}>
            {(settings) => (
              <SwitchRow
                id="classifier"
                title="Task detection"
                help="Runs one small extra model call on each message to spot work requests. Without it MAYA can still start a task when it decides a request needs work."
                checked={
                  settings.classifier_available && settings.intent_classifier
                }
                disabled={!settings.classifier_available || busy}
                stateText={
                  settings.classifier_available
                    ? stateFor(
                        chat,
                        "intent_classifier",
                        settings.intent_classifier,
                      )
                    : "Not available on this server"
                }
                note={
                  settings.classifier_available
                    ? undefined
                    : "The server operator has turned this check off (setting MAYA_CHAT_INTENT_CLASSIFIER)."
                }
                onToggle={() => chat.onToggle("intent_classifier")}
              />
            )}
          </ChatLoading>
        </div>
      </SettingsCard>
    </div>
  );
}

/* -------------------------------------------------------------------------- Memory and data */

const LINK = "text-maya-link underline underline-offset-2 hover:text-maya-text";

export function MemoryPanel({
  chat,
}: {
  readonly chat: ChatSettingsModel;
}): React.JSX.Element {
  const busy = chat.saveState.state === "saving";
  return (
    <div className="grid min-w-0 gap-4">
      <SettingsCard title="Memory">
        <ChatLoading model={chat}>
          {(settings) => (
            <SwitchRow
              id="memory"
              title="Memory in chat and voice"
              help="Lets MAYA save and recall your memories in text and voice. Turning it off stops MAYA from saving or recalling memories in text and voice. Memories you already have stay on the Memory page."
              checked={settings.memory_in_chat}
              disabled={busy}
              stateText={stateFor(
                chat,
                "memory_in_chat",
                settings.memory_in_chat,
              )}
              onToggle={() => chat.onToggle("memory_in_chat")}
            />
          )}
        </ChatLoading>
        <SettingRow
          id="memory-page"
          title="Your saved memories"
          help="See every memory MAYA keeps about you and remove the ones you do not want."
        >
          <Link href="/memory" className={LINK}>
            Open the Memory page
          </Link>
        </SettingRow>
      </SettingsCard>
      <SettingsCard
        title="Danger zone"
        description="Removing data cannot be undone. Each page asks you to confirm and lists exactly what goes."
        tone="danger"
      >
        <SettingRow
          id="del-tasks"
          title="Delete tasks"
          help="Remove finished or failed tasks from the Tasks page."
        >
          <Link href="/tasks" className={LINK}>
            Go to Tasks
          </Link>
        </SettingRow>
        <SettingRow
          id="del-files"
          title="Delete generated files"
          help="Remove files MAYA created from the Artifacts page."
        >
          <Link href="/artifacts" className={LINK}>
            Go to Artifacts
          </Link>
        </SettingRow>
        <SettingRow
          id="del-chats"
          title="Delete conversations"
          help="Remove a conversation from the chat history in the conversation list."
        >
          <Link href="/chat" className={LINK}>
            Go to Chat
          </Link>
        </SettingRow>
        <SettingRow
          id="del-uploads"
          title="Delete uploaded files"
          help="Remove files you uploaded under Files and working folder."
        >
          <Link href="#settings-files" className={LINK}>
            Go to Files
          </Link>
        </SettingRow>
      </SettingsCard>
    </div>
  );
}

/* ---------------------------------------------------------------------- Privacy and security */

export function PrivacyPanel({
  account,
  signingOut,
  onSignOut,
}: {
  readonly account: AccountModel;
  readonly signingOut: boolean;
  readonly onSignOut: () => void;
}): React.JSX.Element {
  const { principal } = account;
  return (
    <div className="grid min-w-0 gap-4">
      <SettingsCard title="Account and session">
        {account.state === "loading" ? (
          <div aria-busy="true" aria-label="Loading account" className="p-4">
            <Skeleton lines={3} />
          </div>
        ) : account.state === "error" ? (
          <div className="p-4">
            <ErrorState
              title="Your account details could not be loaded"
              message="The server did not answer. Your session may have ended."
              onRetry={account.onRetry}
            />
          </div>
        ) : (
          <dl className="m-0 min-w-0 divide-y divide-maya-border">
            {principal?.email !== undefined && (
              <Fact label="Email">
                <WrapText>{principal.email}</WrapText>
              </Fact>
            )}
            <Fact label="User ID">
              <WrapText className="font-mono text-maya-mono">
                {principal?.user_id}
              </WrapText>
            </Fact>
            <Fact label="Workspace ID">
              <WrapText className="font-mono text-maya-mono">
                {principal?.workspace_id}
              </WrapText>
            </Fact>
            <Fact label="Role">
              <WrapText>{principal?.role}</WrapText>
            </Fact>
          </dl>
        )}
        <SettingRow
          id="signout"
          title="Sign out"
          help="Ends your session on this device and returns to the sign-in screen."
        >
          <Button
            variant="secondary"
            leftIcon={<LogOut size={16} aria-hidden="true" />}
            loading={signingOut}
            onClick={onSignOut}
          >
            Sign out
          </Button>
        </SettingRow>
      </SettingsCard>
      <SettingsCard title="What is stored and what leaves this machine">
        <div className="grid gap-1 px-4 py-3">
          <p className="m-0 text-maya-body font-medium">
            What leaves this machine
          </p>
          <p className="m-0 text-maya-small text-maya-secondary">
            MAYA sends your text and voice to the Gemini API to get answers, and
            to Google Search grounding when a task needs web research. Tasks can
            also fetch the web pages they read. Any connection you set up under
            Connections is contacted when a task uses it.
          </p>
        </div>
        <div className="grid gap-1 px-4 py-3">
          <p className="m-0 text-maya-body font-medium">Your credentials</p>
          <p className="m-0 text-maya-small text-maya-secondary">
            Mailbox, Notion and GitHub credentials are stored encrypted for your
            account only, are never shown again, and are used only by your own
            tasks. Disconnecting deletes the stored secret.
          </p>
        </div>
        <div className="grid gap-1 px-4 py-3">
          <p className="m-0 text-maya-body font-medium">Your data</p>
          <p className="m-0 text-maya-small text-maya-secondary">
            Saved memories, tasks and files are listed on the{" "}
            <Link href="/memory" className={LINK}>
              Memory
            </Link>
            ,{" "}
            <Link href="/tasks" className={LINK}>
              Tasks
            </Link>{" "}
            and{" "}
            <Link href="/artifacts" className={LINK}>
              Artifacts
            </Link>{" "}
            pages, where you can remove them.
          </p>
        </div>
      </SettingsCard>
    </div>
  );
}

/* --------------------------------------------------------------------------------- About */

export function AboutPanel({
  account,
}: {
  readonly account: AccountModel;
}): React.JSX.Element {
  const [desktop, setDesktop] = useState(false);
  useEffect(() => {
    setDesktop(getFolderPicker() !== null);
  }, []);
  const apiOk =
    account.health?.status === "ready" || account.health?.status === "alive";
  return (
    <SettingsCard title="This app">
      <dl className="m-0 min-w-0 divide-y divide-maya-border">
        <Fact label="Version">{packageInfo.version}</Fact>
        <Fact label="Running in">
          {desktop ? "MAYA desktop app" : "A web browser"}
        </Fact>
        <Fact label="Server">
          {account.state === "loading" ? (
            "Checking…"
          ) : (
            <Badge tone={apiOk ? "success" : "warning"}>
              {apiOk ? "Reachable" : "Not reachable"}
            </Badge>
          )}
        </Fact>
      </dl>
    </SettingsCard>
  );
}
