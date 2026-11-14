import { describe, expect, it } from "vitest";

import {
  buildThreadItems,
  collectTaskRefs,
  linkPersistedTurns,
  type TimedChatMessage,
  type VoiceTurnInput,
} from "./threadItems";

function msg(
  key: string,
  role: "user" | "assistant",
  createdAt: number,
  extra: Partial<TimedChatMessage> = {},
): TimedChatMessage {
  return {
    key,
    id: null,
    role,
    content: key,
    state: "complete",
    finishReason: null,
    usage: null,
    createdAt,
    ...extra,
  };
}

function turn(
  id: string,
  timestamp: number,
  extra: Partial<VoiceTurnInput> = {},
): VoiceTurnInput {
  return {
    id,
    role: "user",
    text: id,
    isFinal: true,
    timestamp,
    ...extra,
  };
}

describe("buildThreadItems", () => {
  it("orders typed, spoken and task items by time", () => {
    const items = buildThreadItems(
      [msg("m1", "user", 1000), msg("m2", "assistant", 4000)],
      [turn("t1", 2000)],
      [{ taskId: "k1", createdAt: 3000 }],
    );
    expect(items.map((i) => i.id)).toEqual([
      "local:m1",
      "voice:t1",
      "task:k1",
      "local:m2",
    ]);
  });

  it("interleaves typed and spoken turns instead of appending voice last", () => {
    const items = buildThreadItems(
      [msg("a", "user", 1), msg("c", "user", 3)],
      [turn("b", 2)],
      [],
      [],
    );
    expect(items.map((i) => i.kind)).toEqual([
      "message",
      "voiceTurn",
      "message",
    ]);
  });

  it("keeps two identical spoken texts (no text dedupe)", () => {
    const items = buildThreadItems(
      [],
      [turn("t1", 1, { text: "yes" }), turn("t2", 2, { text: "yes" })],
      [],
      [],
    );
    expect(items).toHaveLength(2);
  });

  it("shows interim text in place and the final text after", () => {
    const interim = buildThreadItems(
      [],
      [turn("t1", 1, { text: "hel", isFinal: false })],
      [],
      [],
    );
    const final = buildThreadItems(
      [],
      [turn("t1", 1, { text: "hello", isFinal: true })],
      [],
      [],
    );
    expect(interim[0]).toMatchObject({ text: "hel", isFinal: false });
    expect(final).toHaveLength(1);
    expect(final[0]).toMatchObject({ text: "hello", isFinal: true });
  });

  it("lets a stored message replace the live item with the same id", () => {
    const items = buildThreadItems(
      [msg("h", "user", 1, { id: "m-9", modality: "voice" })],
      [turn("t1", 1, { messageId: "m-9" })],
      [],
      [],
    );
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "message", spoken: true });
  });

  it("keys a persisted voice turn by its message id", () => {
    const items = buildThreadItems(
      [],
      [turn("t1", 1, { messageId: "m-1" })],
      [],
      [],
    );
    expect(items[0]?.id).toBe("m-1");
  });

  it("dedupes tasks by id", () => {
    const items = buildThreadItems(
      [],
      [],
      [
        { taskId: "k", createdAt: 1 },
        { taskId: "k", createdAt: 2 },
      ],
    );
    expect(items).toHaveLength(1);
  });

  it("marks typed-while-live turns as typed and keeps system notes", () => {
    const items = buildThreadItems(
      [],
      [turn("t1", 1, { source: "typed" })],
      [],
      [{ id: "n", createdAt: 2, tone: "error", text: "Voice unavailable" }],
    );
    expect(items[0]).toMatchObject({ source: "typed" });
    expect(items[1]).toMatchObject({ kind: "systemNote", tone: "error" });
  });

  it("is stable for equal timestamps", () => {
    const items = buildThreadItems(
      [msg("a", "user", 1), msg("b", "assistant", 1)],
      [],
      [],
      [],
    );
    expect(items.map((i) => i.id)).toEqual(["local:a", "local:b"]);
  });
});

describe("linkPersistedTurns", () => {
  it("claims each stored message once, by role and text", () => {
    const links = linkPersistedTurns(
      [
        turn("t1", 1, { text: "yes" }),
        turn("t2", 2, { text: "yes" }),
        turn("t3", 3, { text: "pending", isFinal: false }),
      ],
      [{ id: "s1", role: "user", content: "yes" }],
    );
    expect(links).toEqual({ t1: "s1" });
  });

  it("links by stored turn_id before any text matching", () => {
    const links = linkPersistedTurns(
      [turn("t1", 1, { text: "same" }), turn("t2", 2, { text: "same" })],
      [
        { id: "s2", role: "user", content: "same", turn_id: "t2" },
        { id: "s1", role: "user", content: "same", turn_id: "t1" },
      ],
    );
    expect(links).toEqual({ t1: "s1", t2: "s2" });
  });

  it("leaves turns that already carry a messageId alone", () => {
    const links = linkPersistedTurns(
      [turn("t1", 1, { text: "hi", messageId: "s9" })],
      [{ id: "s1", role: "user", content: "hi" }],
    );
    expect(links).toEqual({});
  });
});

describe("collectTaskRefs", () => {
  const msg = (id: string, task: unknown) =>
    ({
      id,
      role: "assistant",
      content: "",
      created_at: "2026-11-08T10:00:00Z",
      metadata: { task },
    }) as never;

  it("collects unique task ids in order and ignores malformed metadata", () => {
    const refs = collectTaskRefs([
      msg("1", { task_id: "a", title: "A" }),
      msg("2", { task_id: "a" }),
      msg("3", null),
      msg("4", { task_id: 5 }),
      msg("5", { task_id: "b" }),
    ]);
    expect(refs).toEqual([
      { taskId: "a", title: "A" },
      { taskId: "b", title: undefined },
    ]);
  });

  it("caps the number of rebuilt tasks", () => {
    const many = Array.from({ length: 15 }, (_, i) =>
      msg(String(i), { task_id: `t${i}` }),
    );
    expect(collectTaskRefs(many)).toHaveLength(10);
  });
});

describe("memory chips from live voice", () => {
  it("adds one chip per saved memory, deduped by memory id, in time order", () => {
    const items = buildThreadItems(
      [],
      [],
      [],
      [],
      [
        { memoryId: "m1", text: "likes tea", updated: false, timestamp: 5 },
        { memoryId: "m1", text: "likes tea", updated: false, timestamp: 5 },
      ],
    );
    expect(items.map((i) => i.kind)).toEqual(["memoryChip"]);
  });
});

describe("buildThreadItems tool-posted voice messages", () => {
  it("shows the live message once and lets the stored one replace it", () => {
    const live = {
      id: "chat:m9",
      role: "assistant" as const,
      text: "hello",
      isFinal: true,
      timestamp: 5,
      messageId: "m9",
      markdown: true,
    };
    const first = buildThreadItems([], [live], []);
    expect(first).toHaveLength(1);
    expect(first[0]).toMatchObject({
      kind: "voiceTurn",
      id: "m9",
      markdown: true,
    });

    const stored = {
      key: "k",
      id: "m9",
      role: "assistant",
      content: "hello",
      state: "done",
      createdAt: 5,
    } as unknown as TimedChatMessage;
    const after = buildThreadItems([stored], [live], []);
    expect(after).toHaveLength(1);
    expect(after[0].kind).toBe("message");
  });
});
