"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import { signOut } from "../../lib/auth-client";
import {
  getChatSettings,
  updateChatSettings,
  type ChatSettings,
} from "../../lib/settings-client";
import {
  checkSystemHealth,
  getPrincipalMe,
  type SystemHealth,
  type UserPrincipal,
} from "../../lib/system-client";
import { Notice, Pane } from "../ui";
import { AgentsSection } from "./AgentsSection";
import { ConnectionsSection } from "./ConnectionsSection";
import { FilesSection } from "./FilesSection";
import { SchedulesSection } from "./SchedulesSection";
import {
  AboutPanel,
  GeneralPanel,
  MemoryPanel,
  PrivacyPanel,
  type AccountModel,
  type ChatSettingsModel,
  type SaveState,
  type SwitchKey,
} from "./panels";
import { SectionHeader, SettingsCard } from "./primitives";
import { SettingsNav } from "./SettingsNav";
import {
  DEFAULT_SECTION,
  sectionDef,
  sectionFromHash,
  type SectionKey,
} from "./sections";

/**
 * Settings shell: a sticky section list on the left (a select on narrow windows), one section at
 * a time on the right. The section lives in the URL hash, so /settings#connections and the
 * existing /settings#settings-files links open the right place, and Back works.
 *
 * Saving rule, the same everywhere: switches and choices save at once and say "Saving..." then
 * "Saved" beside the control; credentials have an explicit Save button.
 */
export function SettingsView(): React.JSX.Element {
  const [active, setActive] = useState<SectionKey>(DEFAULT_SECTION);
  const [principal, setPrincipal] = useState<UserPrincipal | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [accountState, setAccountState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [chatSettings, setChatSettings] = useState<ChatSettings | null>(null);
  const [chatLoadFailed, setChatLoadFailed] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<{
    key: SwitchKey | null;
    state: SaveState;
  }>({ key: null, state: "idle" });
  const [signingOut, setSigningOut] = useState<boolean>(false);
  const mounted = useRef<boolean>(true);
  const heading = useRef<HTMLHeadingElement>(null);
  const moveFocus = useRef<boolean>(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // The hash is the source of truth for the section (deep links, Back and Forward).
  useEffect(() => {
    const read = (): void => {
      setActive(sectionFromHash(window.location.hash) ?? DEFAULT_SECTION);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  // After the person picks a section, focus its title so keyboard and screen readers land there.
  useEffect(() => {
    if (moveFocus.current) {
      moveFocus.current = false;
      heading.current?.focus();
    }
  }, [active]);

  const select = (key: SectionKey): void => {
    moveFocus.current = true;
    const id = sectionDef(key).id;
    if (window.location.hash === `#${id}`) {
      setActive(key);
      return;
    }
    window.location.hash = id;
  };

  const loadAccount = useCallback(() => {
    setAccountState("loading");
    void Promise.all([
      getPrincipalMe().catch(() => null),
      checkSystemHealth().catch(() => ({ status: "unavailable" as const })),
    ]).then(([me, status]) => {
      if (!mounted.current) return;
      setPrincipal(me);
      setHealth(status);
      setAccountState(me ? "ready" : "error");
    });
  }, []);

  const loadChat = useCallback(() => {
    setChatLoadFailed(false);
    getChatSettings()
      .then((value) => {
        if (mounted.current) setChatSettings(value);
      })
      .catch(() => {
        if (mounted.current) setChatLoadFailed(true);
      });
  }, []);

  useEffect(() => {
    loadAccount();
    loadChat();
  }, [loadAccount, loadChat]);

  /** Saves at once. The switch moves first and goes back if the server refuses. */
  const toggle = async (key: SwitchKey): Promise<void> => {
    if (!chatSettings || saveState.state === "saving") return;
    if (key === "intent_classifier" && !chatSettings.classifier_available) {
      return;
    }
    const previous = chatSettings;
    const next = !previous[key];
    setChatSettings({ ...previous, [key]: next });
    setSaveError(null);
    setSaveState({ key, state: "saving" });
    try {
      const saved = await updateChatSettings({ [key]: next });
      if (!mounted.current) return;
      setChatSettings(saved);
      setSaveState({ key, state: "saved" });
    } catch {
      if (!mounted.current) return;
      setChatSettings(previous);
      setSaveError("The setting could not be saved. Try again.");
      setSaveState({ key: null, state: "idle" });
    }
  };

  const handleSignOut = async (): Promise<void> => {
    setSigningOut(true);
    try {
      await signOut();
    } catch {
      // The local session is dropped either way; the sign-in screen shows the real state.
    }
    window.location.href = "/login";
  };

  const chat: ChatSettingsModel = {
    settings: chatSettings,
    failed: chatLoadFailed,
    saveState,
    onRetry: loadChat,
    onToggle: (key) => void toggle(key),
  };
  const account: AccountModel = {
    principal,
    health,
    state: accountState,
    onRetry: loadAccount,
  };

  const def = sectionDef(active);
  let body: React.ReactNode;
  switch (active) {
    case "general":
      body = <GeneralPanel chat={chat} />;
      break;
    case "connections":
      body = <ConnectionsSection />;
      break;
    case "files":
      body = (
        <SettingsCard>
          <div className="min-w-0 px-4">
            <FilesSection />
          </div>
        </SettingsCard>
      );
      break;
    case "schedules":
      body = <SchedulesSection accountEmail={principal?.email ?? null} />;
      break;
    case "agents":
      body = (
        <SettingsCard>
          <div className="min-w-0 px-4">
            <AgentsSection />
          </div>
        </SettingsCard>
      );
      break;
    case "memory":
      body = <MemoryPanel chat={chat} />;
      break;
    case "privacy":
      body = (
        <PrivacyPanel
          account={account}
          signingOut={signingOut}
          onSignOut={() => void handleSignOut()}
        />
      );
      break;
    default:
      body = <AboutPanel account={account} />;
  }

  return (
    <div className="h-full min-h-0 min-w-0">
      <Pane bodyLabel="Settings" as="div">
        <div className="mx-auto grid w-full max-w-(--maya-column-wide) min-w-0 gap-6 p-4 sm:p-6">
          <h1 className="m-0 text-maya-title font-semibold text-maya-text">
            Settings
          </h1>
          <div className="grid min-w-0 items-start gap-6 md:grid-cols-[13rem_minmax(0,1fr)] md:gap-8">
            <SettingsNav active={active} onSelect={select} />
            <section
              aria-labelledby={def.id}
              className="motion-page grid min-w-0 gap-4"
              data-section={active}
              key={active}
            >
              <SectionHeader
                id={def.id}
                title={def.label}
                description={def.description}
                headingRef={heading}
              />
              {saveError !== null && (
                <Notice tone="danger" onDismiss={() => setSaveError(null)}>
                  {saveError}
                </Notice>
              )}
              {body}
            </section>
          </div>
        </div>
      </Pane>
    </div>
  );
}
