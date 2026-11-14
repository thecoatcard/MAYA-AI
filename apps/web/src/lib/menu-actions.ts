/** Small pure helpers shared by the context menus. No network, no React. */

/** The longest text a selection menu action sends in a chat message. */
export const SELECTION_LIMIT = 2000;

/** Collapses the selection to a sendable size (whole code points, trimmed). */
export function clampSelection(text: string): string {
  const trimmed = text.trim();
  const points = Array.from(trimmed);
  return points.length <= SELECTION_LIMIT
    ? trimmed
    : `${points.slice(0, SELECTION_LIMIT).join("")}…`;
}

export type SelectionAction =
  | "explain"
  | "translate-hi"
  | "translate-en"
  | "summarize"
  | "search"
  | "remember";

/**
 * A short natural-language chat message for a selection action. The menu only sends this text;
 * it never runs anything itself. Quoting uses a fenced block so the text stays plain data.
 */
export function selectionPrompt(
  action: SelectionAction,
  selected: string,
): string {
  const text = clampSelection(selected);
  switch (action) {
    case "explain":
      return `Explain this: "${text}"`;
    case "translate-hi":
      return `Translate this to Hindi: "${text}"`;
    case "translate-en":
      return `Translate this to English: "${text}"`;
    case "summarize":
      return `Summarize this: "${text}"`;
    case "search":
      return `Search the web for: ${text}`;
    case "remember":
      return `Remember this: ${text}`;
  }
}

/** "> line" quoting for Quote in reply. Ends with a blank line so the user types below it. */
export function quoteText(text: string): string {
  const body = clampSelection(text)
    .split(/\r?\n/)
    .map((line) => `> ${line}`)
    .join("\n");
  return `${body}\n\n`;
}

/** Copies text. Resolves false when the clipboard is not available. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}

/** Saves text as a file through a blob download (no server call). */
export function downloadText(
  filename: string,
  text: string,
  mime = "text/plain",
): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** A filesystem-safe file name from a title. */
export function safeFileName(title: string, ext: string): string {
  const base =
    title
      .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ")
      .trim()
      .replace(/\s+/g, "-")
      .slice(0, 80) || "conversation";
  return `${base}.${ext}`;
}

/** File extension for a fence language label. */
export function extensionForLanguage(language: string | null): string {
  const map: Record<string, string> = {
    javascript: "js",
    js: "js",
    typescript: "ts",
    ts: "ts",
    tsx: "tsx",
    jsx: "jsx",
    python: "py",
    py: "py",
    json: "json",
    html: "html",
    css: "css",
    bash: "sh",
    sh: "sh",
    shell: "sh",
    sql: "sql",
    yaml: "yml",
    yml: "yml",
    markdown: "md",
    md: "md",
  };
  return map[(language ?? "").toLowerCase()] ?? "txt";
}

/** Opens a link in a new tab the same way the rendered links do (no opener, no referrer). */
export function openLink(href: string): void {
  window.open(href, "_blank", "noopener,noreferrer");
}
