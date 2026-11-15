import fs from "node:fs";
import path from "node:path";
import { redact } from "./policy.mjs";

/**
 * Size-rotated log file (api.log, api.1.log, ...). Writes are synchronous: the volume is small and
 * the last lines survive a crash. Redaction happens before anything is written.
 */
export class RotatingLog {
  /** @param {{ dir: string, name?: string, maxBytes?: number, keep?: number, home?: string }} opts */
  constructor({ dir, name = "api", maxBytes = 2_000_000, keep = 3, home }) {
    this.dir = dir;
    this.name = name;
    this.maxBytes = maxBytes;
    this.keep = keep;
    this.home = home;
    fs.mkdirSync(dir, { recursive: true });
  }

  get file() {
    return path.join(this.dir, `${this.name}.log`);
  }

  /** @param {string | Buffer} chunk */
  write(chunk) {
    const text = redact(chunk.toString(), this.home);
    try {
      if (fs.existsSync(this.file) && fs.statSync(this.file).size + text.length > this.maxBytes) {
        this.#rotate();
      }
      fs.appendFileSync(this.file, text);
    } catch {
      // Logging must never take the app down.
    }
  }

  #rotate() {
    const at = (i) => path.join(this.dir, i === 0 ? `${this.name}.log` : `${this.name}.${i}.log`);
    fs.rmSync(at(this.keep - 1), { force: true });
    for (let i = this.keep - 2; i >= 0; i--) {
      if (fs.existsSync(at(i))) fs.renameSync(at(i), at(i + 1));
    }
  }
}
