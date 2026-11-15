/**
 * The Settings sections and how a URL hash maps to one. Every section has a stable id
 * ("settings-connections") that is also the anchor, so /settings#settings-files (used by the
 * working-folder link) and the short form /settings#files both open the right section.
 */

export type SectionKey =
  | "general"
  | "connections"
  | "files"
  | "schedules"
  | "agents"
  | "memory"
  | "privacy"
  | "about";

export interface SectionDef {
  readonly key: SectionKey;
  readonly id: string;
  readonly label: string;
  readonly description: string;
}

export const SECTIONS: readonly SectionDef[] = [
  {
    key: "general",
    id: "settings-general",
    label: "General",
    description: "Appearance and how MAYA handles your messages.",
  },
  {
    key: "connections",
    id: "settings-connections",
    label: "Connections",
    description:
      "Your mailbox, Notion and GitHub, so MAYA can work with your own accounts.",
  },
  {
    key: "files",
    id: "settings-files",
    label: "Files and working folder",
    description: "The folder MAYA may work in and the files you uploaded.",
  },
  {
    key: "schedules",
    id: "settings-schedules",
    label: "Schedules",
    description:
      "Reports MAYA prepares for you at a fixed time, and how they reach you.",
  },
  {
    key: "agents",
    id: "settings-agents",
    label: "Agents",
    description: "The built-in agents and the custom agents you created.",
  },
  {
    key: "memory",
    id: "settings-memory",
    label: "Memory and data",
    description: "What MAYA remembers and where to remove your data.",
  },
  {
    key: "privacy",
    id: "settings-privacy",
    label: "Privacy and security",
    description: "Your account, your session and what leaves this machine.",
  },
  {
    key: "about",
    id: "settings-about",
    label: "About",
    description: "Version and server status.",
  },
];

export const DEFAULT_SECTION: SectionKey = "general";

/** The section a hash points to ("#settings-files", "#files", "#Connections"), else null. */
export function sectionFromHash(hash: string): SectionKey | null {
  const raw = hash.replace(/^#/, "").trim().toLowerCase();
  if (raw === "") return null;
  const short = raw.startsWith("settings-")
    ? raw.slice("settings-".length)
    : raw;
  const alias: Record<string, SectionKey> = {
    mail: "connections",
    mailbox: "connections",
    notion: "connections",
    github: "connections",
    schedule: "schedules",
    reports: "schedules",
    folder: "files",
    "working-folder": "files",
    data: "memory",
    security: "privacy",
    account: "privacy",
  };
  const found = SECTIONS.find((section) => section.key === short);
  return found?.key ?? alias[short] ?? null;
}

export function sectionDef(key: SectionKey): SectionDef {
  return SECTIONS.find((section) => section.key === key) ?? SECTIONS[0]!;
}
