/**
 * Helpers shared by the chat tests. Not imported by application code.
 *
 * They build SSE text and mocked fetch responses backed by a real
 * ReadableStream, so the real parser, decoder and client code run in tests
 * with no network.
 */

const encoder = new TextEncoder();

export function frame(event: string, data: unknown, id?: number): string {
  const idLine = id === undefined ? "" : `id: ${id}\n`;
  return `${idLine}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export function acceptedFrame(
  options: { replayed?: boolean; sessionId?: string } = {},
): string {
  return frame(
    "message.accepted",
    {
      session_id: options.sessionId ?? "11111111-1111-4111-8111-111111111111",
      user_message_id: "user-msg-1",
      assistant_message_id: "assistant-msg-1",
      replayed: options.replayed ?? false,
    },
    1,
  );
}

export function deltaFrame(text: string): string {
  return frame("delta", { text });
}

export function doneFrame(finishReason = "stop"): string {
  return frame("done", {
    assistant_message_id: "assistant-msg-1",
    finish_reason: finishReason,
    usage: {
      prompt_tokens: 4,
      candidates_tokens: 6,
      total_tokens: 10,
      cost_usd: null,
      is_estimate: true,
    },
  });
}

export function errorFrame(code: string, retryable: boolean): string {
  return frame("error", { code, retryable });
}

export function toBytes(chunk: string | Uint8Array): Uint8Array {
  return typeof chunk === "string" ? encoder.encode(chunk) : chunk;
}

/**
 * A stream that delivers the given chunks one read at a time, then closes (or
 * fails with `failure`). Pull-based, so every chunk is read before the failure.
 */
export function streamFromChunks(
  chunks: ReadonlyArray<string | Uint8Array>,
  failure?: unknown,
): ReadableStream<Uint8Array> {
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(toBytes(chunks[index]));
        index += 1;
      } else if (failure === undefined) {
        controller.close();
      } else {
        controller.error(failure);
      }
    },
  });
}

export interface ControllableStream {
  stream: ReadableStream<Uint8Array>;
  push: (chunk: string | Uint8Array) => void;
  close: () => void;
  fail: (error: unknown) => void;
  /** True once the consumer cancelled the stream. */
  readonly cancelled: boolean;
}

/** A stream the test feeds by hand, to model slow or hanging responses. */
export function controllableStream(): ControllableStream {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
    cancel() {
      cancelled = true;
    },
  });
  return {
    stream,
    push: (chunk) => controller.enqueue(toBytes(chunk)),
    close: () => controller.close(),
    fail: (error) => controller.error(error),
    get cancelled() {
      return cancelled;
    },
  };
}

export function sseResponse(
  body: ReadableStream<Uint8Array> | ReadonlyArray<string | Uint8Array>,
  init: { status?: number; headers?: Record<string, string> } = {},
): Response {
  const stream = Array.isArray(body)
    ? streamFromChunks(body as ReadonlyArray<string | Uint8Array>)
    : (body as ReadableStream<Uint8Array>);
  return new Response(stream, {
    status: init.status ?? 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store",
      ...init.headers,
    },
  });
}

export function jsonResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** Collect an async iterable into an array. */
export async function collect<T>(iterable: AsyncIterable<T>): Promise<T[]> {
  const items: T[] = [];
  for await (const item of iterable) {
    items.push(item);
  }
  return items;
}

/** A tiny deterministic PRNG so fuzz tests are repeatable. */
export function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}
