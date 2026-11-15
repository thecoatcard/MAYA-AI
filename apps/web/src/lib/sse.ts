/**
 * Incremental Server-Sent Events (SSE) parser.
 *
 * `SseParser` turns decoded text chunks into frames. It accepts frames split at
 * any point, CRLF, LF and lone CR line endings (including a CRLF split across
 * two chunks), several `data:` lines per frame, comment lines (`:`) and unknown
 * fields (ignored). A frame without a `data:` line is dropped, as in the SSE
 * specification, and an incomplete final frame is never dispatched.
 *
 * `readSseFrames` adds byte decoding on top: it reads a `ReadableStream` of
 * `Uint8Array` chunks with a streaming `TextDecoder`, so a multi-byte character
 * split across two chunks is decoded correctly.
 *
 * Nothing here uses a regular expression on unbounded input, and each character
 * is visited once, so work stays linear in the input size.
 */

export interface SseFrame {
  /** The `event:` field, or "message" when the frame had none. */
  readonly event: string;
  /** All `data:` lines joined with "\n". */
  readonly data: string;
  /** The `id:` field of this frame, or null. */
  readonly id: string | null;
  /** The `retry:` field in milliseconds, or null. */
  readonly retry: number | null;
}

/** Thrown when a single frame or line grows beyond the safety limit. */
export class SseBufferOverflowError extends Error {
  constructor() {
    super("The event stream sent a frame that is too large.");
    this.name = "SseBufferOverflowError";
  }
}

/** Upper bound for one unfinished line or frame, in UTF-16 code units. */
export const SSE_MAX_BUFFER_CHARS = 4 * 1024 * 1024;

const CODE_LF = 10;
const CODE_CR = 13;
const BYTE_ORDER_MARK = 0xfeff;

export class SseParser {
  /** Pieces of the unfinished line, joined only once its terminator arrives. */
  private partial: string[] = [];
  private partialChars = 0;
  /** The previous chunk ended with CR: a LF at the start of this one is part of it. */
  private skipLeadingLf = false;
  private started = false;
  private eventName = "";
  private dataLines: string[] = [];
  private hasData = false;
  private pendingChars = 0;
  private frameId: string | null = null;
  private frameRetry: number | null = null;
  private readonly maxChars: number;

  constructor(maxChars: number = SSE_MAX_BUFFER_CHARS) {
    this.maxChars = maxChars;
  }

  /** Feed the next decoded text chunk; returns the frames it completed. */
  push(chunk: string): SseFrame[] {
    let text = chunk;
    if (!this.started && text.length > 0) {
      this.started = true;
      if (text.charCodeAt(0) === BYTE_ORDER_MARK) {
        text = text.slice(1);
      }
    }
    const frames: SseFrame[] = [];
    const length = text.length;
    let lineStart = 0;
    if (this.skipLeadingLf && length > 0) {
      this.skipLeadingLf = false;
      if (text.charCodeAt(0) === CODE_LF) {
        lineStart = 1;
      }
    }
    // Each character is visited once, and a long line split over many chunks is
    // only joined when it ends, so the work stays linear in the input size.
    for (let i = lineStart; i < length; i += 1) {
      const code = text.charCodeAt(i);
      if (code !== CODE_LF && code !== CODE_CR) {
        continue;
      }
      if (this.partialChars + i - lineStart > this.maxChars) {
        throw new SseBufferOverflowError();
      }
      this.handleLine(this.takeLine(text.slice(lineStart, i)), frames);
      if (this.pendingChars > this.maxChars) {
        throw new SseBufferOverflowError();
      }
      if (code === CODE_CR) {
        if (i + 1 < length) {
          if (text.charCodeAt(i + 1) === CODE_LF) {
            i += 1;
          }
        } else {
          this.skipLeadingLf = true;
        }
      }
      lineStart = i + 1;
    }
    if (lineStart < length) {
      const rest = text.slice(lineStart);
      this.partial.push(rest);
      this.partialChars += rest.length;
    }
    if (
      this.partialChars > this.maxChars ||
      this.pendingChars > this.maxChars
    ) {
      throw new SseBufferOverflowError();
    }
    return frames;
  }

  /**
   * Signal the end of the stream. An unfinished line or frame is discarded, as
   * the SSE specification requires.
   */
  end(): SseFrame[] {
    this.partial = [];
    this.partialChars = 0;
    this.skipLeadingLf = false;
    this.resetFrame();
    return [];
  }

  private takeLine(tail: string): string {
    if (this.partial.length === 0) {
      return tail;
    }
    const line = this.partial.join("") + tail;
    this.partial = [];
    this.partialChars = 0;
    return line;
  }

  private handleLine(line: string, frames: SseFrame[]): void {
    if (line.length === 0) {
      this.dispatch(frames);
      return;
    }
    if (line.charCodeAt(0) === 58 /* ":" */) {
      return; // comment line
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.charCodeAt(0) === 32) {
      value = value.slice(1);
    }
    switch (field) {
      case "event":
        this.eventName = value;
        this.pendingChars += value.length;
        break;
      case "data":
        this.dataLines.push(value);
        this.hasData = true;
        this.pendingChars += value.length + 1;
        break;
      case "id":
        if (!value.includes("\u0000")) {
          this.frameId = value;
        }
        break;
      case "retry":
        if (isAllAsciiDigits(value)) {
          this.frameRetry = Number.parseInt(value, 10);
        }
        break;
      default:
        break; // unknown field: ignored
    }
  }

  private dispatch(frames: SseFrame[]): void {
    if (this.hasData) {
      frames.push({
        event: this.eventName === "" ? "message" : this.eventName,
        data: this.dataLines.join("\n"),
        id: this.frameId,
        retry: this.frameRetry,
      });
    }
    this.resetFrame();
  }

  private resetFrame(): void {
    this.eventName = "";
    this.dataLines = [];
    this.hasData = false;
    this.pendingChars = 0;
    this.frameId = null;
    this.frameRetry = null;
  }
}

function isAllAsciiDigits(value: string): boolean {
  if (value.length === 0 || value.length > 9) {
    return false;
  }
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code < 48 || code > 57) {
      return false;
    }
  }
  return true;
}

function abortError(): Error {
  const error = new Error("The operation was aborted.");
  error.name = "AbortError";
  return error;
}

/**
 * Read SSE frames from a byte stream.
 *
 * Throws an `AbortError` when `signal` is aborted (even when the underlying
 * stream just ends), so a cut-off stream is never mistaken for a finished one.
 * The reader is always cancelled when the consumer stops iterating.
 */
export async function* readSseFrames(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame, void, undefined> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  const cancelReader = () => {
    reader.cancel().catch(() => undefined);
  };
  if (signal) {
    if (signal.aborted) {
      cancelReader();
    } else {
      signal.addEventListener("abort", cancelReader, { once: true });
    }
  }
  try {
    for (;;) {
      const result = await reader.read();
      if (signal?.aborted) {
        throw abortError();
      }
      if (result.done) {
        break;
      }
      const text = decoder.decode(result.value, { stream: true });
      for (const frame of parser.push(text)) {
        yield frame;
      }
    }
    for (const frame of parser.push(decoder.decode())) {
      yield frame;
    }
    for (const frame of parser.end()) {
      yield frame;
    }
  } finally {
    signal?.removeEventListener("abort", cancelReader);
    cancelReader();
  }
}
