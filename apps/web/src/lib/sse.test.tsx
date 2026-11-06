import { describe, expect, it } from "vitest";

import {
  collect,
  controllableStream,
  streamFromChunks,
} from "../components/chat/chat-test-utils";
import {
  readSseFrames,
  SseBufferOverflowError,
  SseParser,
  type SseFrame,
} from "./sse";

function parseAll(chunks: string[]): SseFrame[] {
  const parser = new SseParser();
  const frames: SseFrame[] = [];
  for (const chunk of chunks) {
    frames.push(...parser.push(chunk));
  }
  frames.push(...parser.end());
  return frames;
}

describe("SseParser", () => {
  it("parses a complete frame", () => {
    expect(parseAll(['id: 1\nevent: delta\ndata: {"text":"hi"}\n\n'])).toEqual([
      { event: "delta", data: '{"text":"hi"}', id: "1", retry: null },
    ]);
  });

  it("defaults the event name to message", () => {
    expect(parseAll(["data: x\n\n"])[0]?.event).toBe("message");
  });

  it("yields the same frames when split at every character boundary", () => {
    const text =
      'id: 1\nevent: message.accepted\ndata: {"a":1}\n\nevent: delta\ndata: {"text":"héllo"}\n\n';
    const whole = parseAll([text]);
    expect(whole).toHaveLength(2);
    for (let cut = 1; cut < text.length; cut += 1) {
      expect(parseAll([text.slice(0, cut), text.slice(cut)])).toEqual(whole);
    }
    expect(parseAll(Array.from(text))).toEqual(whole);
  });

  it("accepts LF, CRLF and CR line endings", () => {
    const lf = parseAll(["event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"]);
    expect(
      parseAll(["event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n"]),
    ).toEqual(lf);
    expect(parseAll(["event: a\rdata: 1\r\revent: b\rdata: 2\r\r"])).toEqual(
      lf,
    );
  });

  it("does not treat a CRLF split across chunks as two line endings", () => {
    const whole = parseAll(["event: a\r\ndata: 1\r\n\r\n"]);
    expect(whole).toHaveLength(1);
    expect(parseAll(["event: a\r", "\ndata: 1\r", "\n\r", "\n"])).toEqual(
      whole,
    );
    expect(parseAll(["event: a\r\ndata: 1\r\n\r", "\n"])).toEqual(whole);
  });

  it("joins multiple data lines with a newline", () => {
    expect(parseAll(["data: one\ndata: two\ndata: three\n\n"])[0]?.data).toBe(
      "one\ntwo\nthree",
    );
  });

  it("ignores comment lines, including keep-alives between frames", () => {
    const frames = parseAll([
      ": keep-alive\n\n",
      "event: a\n: a comment inside\ndata: 1\n\n",
      ":\n",
    ]);
    expect(frames).toEqual([{ event: "a", data: "1", id: null, retry: null }]);
  });

  it("ignores unknown fields and keeps the known ones", () => {
    const frames = parseAll(["foo: bar\nevent: a\nbaz\ndata: 1\n\n"]);
    expect(frames).toEqual([{ event: "a", data: "1", id: null, retry: null }]);
  });

  it("strips only a single space after the colon", () => {
    expect(parseAll(["data:  two spaces\n\n"])[0]?.data).toBe(" two spaces");
    expect(parseAll(["data:nospace\n\n"])[0]?.data).toBe("nospace");
  });

  it("treats a field name without a colon as an empty value", () => {
    expect(parseAll(["data\ndata: x\n\n"])[0]?.data).toBe("\nx");
  });

  it("reads retry only when it is all digits and ignores ids with NUL", () => {
    expect(parseAll(["retry: 1500\ndata: x\n\n"])[0]?.retry).toBe(1500);
    expect(parseAll(["retry: soon\ndata: x\n\n"])[0]?.retry).toBeNull();
    expect(parseAll(["id: a\u0000b\ndata: x\n\n"])[0]?.id).toBeNull();
  });

  it("drops a frame without data and never dispatches an incomplete frame", () => {
    expect(parseAll(["event: lonely\n\n"])).toEqual([]);
    expect(parseAll(["event: a\ndata: 1\n\nevent: b\ndata: 2"])).toHaveLength(
      1,
    );
    expect(parseAll(["event: a\ndata: 1\n"])).toEqual([]);
  });

  it("strips a leading byte order mark", () => {
    expect(parseAll(["﻿data: x\n\n"])[0]?.data).toBe("x");
  });

  it("does not let state leak from one frame into the next", () => {
    const frames = parseAll([
      "id: 7\nretry: 10\nevent: a\ndata: 1\n\ndata: 2\n\n",
    ]);
    expect(frames[1]).toEqual({
      event: "message",
      data: "2",
      id: null,
      retry: null,
    });
  });

  it("rejects a runaway line instead of buffering it forever", () => {
    const parser = new SseParser(100);
    expect(() => parser.push("data: " + "x".repeat(200))).toThrow(
      SseBufferOverflowError,
    );
    const parser2 = new SseParser(100);
    expect(() => {
      for (let i = 0; i < 100; i += 1) {
        parser2.push("data: xxxxxxxxxx\n");
      }
    }).toThrow(SseBufferOverflowError);
  });

  it.each([
    ["data", "data: "],
    ["id", "id: "],
    ["comment", ": "],
    ["unknown field", "other: "],
  ])("rejects an oversized complete %s line in one chunk", (_name, prefix) => {
    const parser = new SseParser(16);
    expect(() => parser.push(prefix + "x".repeat(20) + "\n\n")).toThrow(
      SseBufferOverflowError,
    );
  });

  it("rejects a complete oversized frame made of individually bounded lines", () => {
    const parser = new SseParser(16);
    expect(() =>
      parser.push("data: 12345\ndata: 67890\ndata: 12345\n\n"),
    ).toThrow(SseBufferOverflowError);
  });

  it("rejects a line that crosses the limit in the chunk that terminates it", () => {
    const parser = new SseParser(16);
    parser.push("data: 12345");
    expect(() => parser.push("67890x\n\n")).toThrow(SseBufferOverflowError);
  });

  it("accepts a line at the limit and multiple bounded frames in one chunk", () => {
    const parser = new SseParser(16);
    expect(parser.push("data: 1234567890\n\ndata: ok\n\n")).toEqual([
      { event: "message", data: "1234567890", id: null, retry: null },
      { event: "message", data: "ok", id: null, retry: null },
    ]);
  });

  it("accepts an accumulated frame exactly at the limit", () => {
    const parser = new SseParser(16);
    expect(parser.push("data: 1234567\ndata: 7654321\n\n")).toEqual([
      { event: "message", data: "1234567\n7654321", id: null, retry: null },
    ]);
  });

  it("stays fast for a long line delivered in very small chunks", () => {
    const parser = new SseParser();
    const started = performance.now();
    const line = "data: " + "a".repeat(200_000) + "\n\n";
    const frames: SseFrame[] = [];
    for (let i = 0; i < line.length; i += 7) {
      frames.push(...parser.push(line.slice(i, i + 7)));
    }
    expect(frames).toHaveLength(1);
    expect(frames[0]?.data).toHaveLength(200_000);
    expect(performance.now() - started).toBeLessThan(1500);
  });
});

describe("readSseFrames", () => {
  const encoder = new TextEncoder();

  it("decodes multi-byte characters split across byte chunks", async () => {
    const payload = 'event: delta\ndata: {"text":"€ 😀 नमस्ते"}\n\n';
    const bytes = encoder.encode(payload);
    const expected = await collect(readSseFrames(streamFromChunks([bytes])));
    expect(expected).toHaveLength(1);
    expect(JSON.parse(expected[0]?.data ?? "{}")).toEqual({
      text: "€ 😀 नमस्ते",
    });
    for (let cut = 1; cut < bytes.length; cut += 1) {
      const chunks = [bytes.slice(0, cut), bytes.slice(cut)];
      expect(await collect(readSseFrames(streamFromChunks(chunks)))).toEqual(
        expected,
      );
    }
  });

  it("works one byte at a time", async () => {
    const bytes = encoder.encode('event: delta\ndata: {"text":"😀"}\n\n');
    const chunks = Array.from(bytes, (byte) => new Uint8Array([byte]));
    const frames = await collect(readSseFrames(streamFromChunks(chunks)));
    expect(JSON.parse(frames[0]?.data ?? "{}")).toEqual({ text: "😀" });
  });

  it("does not dispatch a frame cut off by the end of the stream", async () => {
    const frames = await collect(
      readSseFrames(
        streamFromChunks(["event: a\ndata: 1\n\nevent: b\ndata: 2"]),
      ),
    );
    expect(frames.map((frame) => frame.event)).toEqual(["a"]);
  });

  it("propagates a read failure", async () => {
    const stream = streamFromChunks(
      ["event: a\ndata: 1\n\n"],
      new TypeError("network error"),
    );
    const seen: string[] = [];
    await expect(
      (async () => {
        for await (const frame of readSseFrames(stream)) {
          seen.push(frame.event);
        }
      })(),
    ).rejects.toThrow(TypeError);
    expect(seen).toEqual(["a"]);
  });

  it("throws AbortError and cancels the stream when the signal aborts", async () => {
    const controlled = controllableStream();
    const controller = new AbortController();
    const frames = readSseFrames(controlled.stream, controller.signal);
    controlled.push("event: a\ndata: 1\n\n");
    const first = await frames.next();
    expect(first.value).toMatchObject({ event: "a" });
    const pending = frames.next();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(controlled.cancelled).toBe(true);
  });

  it("cancels the stream when the consumer stops early", async () => {
    const controlled = controllableStream();
    controlled.push("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    for await (const frame of readSseFrames(controlled.stream)) {
      expect(frame.event).toBe("a");
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(controlled.cancelled).toBe(true);
  });
});
