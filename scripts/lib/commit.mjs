// Conventional Commits parsing and trailer helpers.

/** Remove git comment lines ("# ...") and trailing whitespace. */
export function cleanMessage(message) {
  return message
    .replace(/\r\n/g, '\n')
    .split('\n')
    .filter((l) => !l.startsWith('#'))
    .join('\n')
    .replace(/\s+$/, '');
}

export function shouldSkip(header, cfg) {
  return (cfg.commits.skipPrefixes ?? []).some((p) => header.startsWith(p));
}

/**
 * Validate the first line against Conventional Commits.
 * @returns {{ ok: boolean, errors: string[], type?: string }}
 */
export function validateHeader(header, cfg) {
  const types = cfg.commits.types;
  const max = cfg.commits.maxHeaderLength;
  const re = new RegExp(`^(${types.join('|')})(\\([a-z0-9._/-]+\\))?(!)?: (\\S.*)$`);
  const m = re.exec(header);
  const errors = [];
  if (!m) {
    errors.push(
      `Header must look like "<type>(<scope>): <summary>" with type one of: ${types.join(', ')}. Got: "${header}"`,
    );
    return { ok: false, errors };
  }
  if (header.length > max) errors.push(`Header is ${header.length} characters; the limit is ${max}.`);
  return { ok: errors.length === 0, errors, type: m[1] };
}

/** Parse the trailing "Key: value" block of a commit message into a Map(lowercase key -> values[]). */
export function parseTrailers(message) {
  const paragraphs = cleanMessage(message).split(/\n\s*\n/);
  const trailers = new Map();
  if (paragraphs.length < 2) return trailers; // first paragraph is the header
  const last = paragraphs[paragraphs.length - 1].split('\n').filter((l) => l.trim() !== '');
  const line = /^([A-Za-z][A-Za-z0-9-]*):\s+(\S.*)$/;
  if (last.length === 0 || !last.every((l) => line.test(l))) return trailers;
  for (const l of last) {
    const m = line.exec(l);
    const key = m[1].toLowerCase();
    trailers.set(key, [...(trailers.get(key) ?? []), m[2].trim()]);
  }
  return trailers;
}

const GENERIC_REASONS = /^(n\/?a|none|no|nothing|-+|\.+|tbd|todo|skip|ok)$/i;

/**
 * A waiver trailer value must read "not-required: <specific reason>".
 * @returns {{ valid: boolean, reason?: string }}
 */
export function parseWaiver(value) {
  const m = /^not-required:\s*(.+)$/i.exec(value.trim());
  if (!m) return { valid: false };
  const reason = m[1].trim();
  if (reason.length < 8 || GENERIC_REASONS.test(reason)) return { valid: false, reason };
  return { valid: true, reason };
}

/** Find a valid waiver for `trailerName` across commit messages. */
export function findWaiver(messages, trailerName) {
  const key = trailerName.toLowerCase();
  for (const message of messages) {
    for (const value of parseTrailers(message).get(key) ?? []) {
      const w = parseWaiver(value);
      if (w.valid) return w.reason;
    }
  }
  return null;
}

/** Does the message carry the approval trailer with a meaningful value? */
export function hasApproval(message, trailerName) {
  const values = parseTrailers(message).get(trailerName.toLowerCase()) ?? [];
  return values.some((v) => v.trim().length >= 3);
}

export function commitType(message, cfg) {
  const header = cleanMessage(message).split('\n')[0] ?? '';
  const v = validateHeader(header, cfg);
  return v.type ?? null;
}
