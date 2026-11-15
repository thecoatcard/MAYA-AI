/**
 * The ask-the-user question card (`ask_user`): wire readers and small pure helpers.
 *
 * MAYA asks a short question inline (choices and/or a text answer). It is NOT an approval: an
 * answer is only the user's reply, and nothing here can approve or start an action. Every string
 * is plain text and is rendered as text, never as Markdown or HTML.
 */

export const MAX_ASK_QUESTIONS = 4;
export const MAX_ASK_OPTIONS = 6;
const MAX_TEXT = 600;

export interface AskOption {
  readonly label: string;
  readonly description: string;
}

export interface AskQuestion {
  readonly question: string;
  /** A short chip label (at most 12 characters); may be empty. */
  readonly header: string;
  /** Empty means a short free-text question. */
  readonly options: readonly AskOption[];
  readonly multiSelect: boolean;
  readonly allowText: boolean;
}

export interface ChatInputRequest {
  readonly askId: string;
  readonly questions: readonly AskQuestion[];
  readonly source: "chat" | "voice" | "task";
}

/** One answered question: the labels chosen and/or the text typed. */
export interface AskAnswerItem {
  readonly selected: readonly string[];
  readonly text: string;
}

export interface ChatInputAnswer {
  readonly askId: string;
  readonly answers: readonly (AskAnswerItem & { readonly question: string })[];
}

/** How the thread shows a card: waiting, answered by the next user message, or dismissed. */
export type AskCardState = "awaiting_input" | "answered" | "dismissed";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, max = MAX_TEXT): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

/** One question from its wire shape; null when it has no text. */
function readQuestion(value: unknown): AskQuestion | null {
  if (!isRecord(value)) return null;
  const question = str(value.question);
  if (question.trim() === "") return null;
  const options: AskOption[] = [];
  if (Array.isArray(value.options)) {
    for (const raw of value.options as unknown[]) {
      if (options.length >= MAX_ASK_OPTIONS) break;
      if (!isRecord(raw)) continue;
      const label = str(raw.label, 120);
      if (label.trim() === "") continue;
      options.push({ label, description: str(raw.description, 240) });
    }
  }
  return {
    question,
    header: str(value.header, 12),
    options,
    multiSelect: value.multi_select === true && options.length > 0,
    // A card without options needs somewhere to answer.
    allowText: options.length === 0 ? true : value.allow_text !== false,
  };
}

/**
 * Read a question card (`input.requested`, `metadata.input_request`, or the voice frame's card).
 * Anything that is not a well-formed card reads as null, so a bad entry is skipped.
 */
export function readInputRequest(value: unknown): ChatInputRequest | null {
  if (!isRecord(value)) return null;
  const askId = str(value.ask_id, 64);
  if (askId === "" || !Array.isArray(value.questions)) return null;
  const questions: AskQuestion[] = [];
  for (const raw of value.questions as unknown[]) {
    if (questions.length >= MAX_ASK_QUESTIONS) break;
    const q = readQuestion(raw);
    if (q !== null) questions.push(q);
  }
  if (questions.length === 0) return null;
  const source =
    value.source === "voice" || value.source === "task" ? value.source : "chat";
  return { askId, questions, source };
}

/** The answer a stored user message carried (`metadata.answers_to` + `metadata.answers`). */
export function readInputAnswer(
  askId: unknown,
  answers: unknown,
): ChatInputAnswer | null {
  if (typeof askId !== "string" || askId === "" || !Array.isArray(answers)) {
    return null;
  }
  const items: (AskAnswerItem & { question: string })[] = [];
  for (const raw of answers as unknown[]) {
    if (!isRecord(raw)) continue;
    items.push({
      question: str(raw.question),
      selected: Array.isArray(raw.selected)
        ? (raw.selected as unknown[])
            .filter((s): s is string => typeof s === "string")
            .slice(0, MAX_ASK_OPTIONS)
        : [],
      text: str(raw.text),
    });
  }
  return items.length === 0 ? null : { askId, answers: items };
}

/** The request body field for an answer (`answer` on POST /chat/message). */
export function answerToWire(
  askId: string,
  answers: readonly AskAnswerItem[],
): { ask_id: string; answers: { selected: string[]; text: string }[] } {
  return {
    ask_id: askId,
    answers: answers.map((a) => ({ selected: [...a.selected], text: a.text })),
  };
}

/** True when the answer carries something: a chosen label or non-blank text. */
export function isAnswered(item: AskAnswerItem): boolean {
  return item.selected.length > 0 || item.text.trim() !== "";
}

/** The label of a question for the readable answer: its chip, or the start of its text. */
export function questionLabel(question: AskQuestion): string {
  return question.header !== ""
    ? question.header
    : question.question.slice(0, 30);
}

/** The plain text shown as the user's message, 'Language: Java; Theme: glass-aurora'. */
export function answerReadableText(
  questions: readonly AskQuestion[],
  answers: readonly AskAnswerItem[],
): string {
  return questions
    .map((q, i) => {
      const a = answers[i];
      const parts =
        a === undefined
          ? []
          : [...a.selected, ...(a.text.trim() !== "" ? [a.text.trim()] : [])];
      return `${questionLabel(q)}: ${parts.length > 0 ? parts.join(", ") : "(skipped)"}`;
    })
    .join("; ");
}

const NEEDS_DETAIL = "need a little more detail";

/**
 * The question of a task step that stopped because a detail was missing (the document agent
 * says "I need a little more detail: <question>"), or null for any other failure.
 */
export function needsDetailQuestion(failure: string | null): string | null {
  if (failure === null || !failure.toLowerCase().includes(NEEDS_DETAIL)) {
    return null;
  }
  const at = failure.indexOf(":");
  const question = (at >= 0 ? failure.slice(at + 1) : failure).trim();
  return question === "" ? "What detail should I use?" : question.slice(0, 300);
}

/** The card a task shows for a missing detail: one short-text question, no options. */
export function taskDetailRequest(
  taskId: string,
  question: string,
): ChatInputRequest {
  return {
    askId: `task-${taskId}`,
    source: "task",
    questions: [
      {
        question,
        header: "Detail",
        options: [],
        multiSelect: false,
        allowText: true,
      },
    ],
  };
}
