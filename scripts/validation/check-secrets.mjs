// Heuristic secret scanner. It is a fast local backstop, NOT a replacement for
// Gitleaks, which runs in CI. It never prints a matched value, only rule and location.
//
// Usage: node scripts/validation/check-secrets.mjs [--all | --staged | --range <base>] [--root <dir>]
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, EXIT, walk, isBinary, isMain, argValue } from '../lib/fsutil.mjs';
import * as G from '../lib/git.mjs';

const PLACEHOLDER =
  /(example|placeholder|changeme|change-me|your[-_ ]|xxxx|dummy|sample|redacted|fake|<[^>]+>|\$\{|\{\{|\*{3,}|todo|replace[-_ ]me|not[-_ ]a[-_ ]real|^(?:password|passwd|secret|pass)$)/i;

export const RULES = [
  { id: 'aws-access-key-id', re: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/ },
  { id: 'github-token', re: /\bgh[pousr]_[A-Za-z0-9]{36,}\b/ },
  { id: 'github-fine-grained-token', re: /\bgithub_pat_[A-Za-z0-9_]{22,}\b/ },
  { id: 'google-api-key', re: /\bAIza[0-9A-Za-z_-]{35}\b/ },
  { id: 'slack-token', re: /\bxox[abprs]-[0-9A-Za-z-]{10,}\b/ },
  { id: 'private-key-block', re: /-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----/ },
  { id: 'anthropic-key', re: /\bsk-ant-[A-Za-z0-9_-]{20,}\b/ },
  { id: 'openai-style-key', re: /\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b/ },
  { id: 'stripe-live-key', re: /\b[sr]k_live_[0-9A-Za-z]{16,}\b/ },
  { id: 'jwt', re: /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/ },
  {
    id: 'url-with-password',
    re: /\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp):\/\/[^\s:@/]+:([^\s@/]{6,})@/,
    group: 1,
    placeholderCheck: true,
  },
  {
    id: 'assigned-secret',
    re: /(?:api[_-]?key|secret|token|passwd|password|private[_-]?key|client[_-]?secret)["']?\s*[:=]\s*["']([^"'\s]{12,})["']/i,
    group: 1,
    placeholderCheck: true,
  },
  {
    id: 'env-style-secret',
    re: /^[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*=([^\s#"']{12,})\s*$/,
    group: 1,
    placeholderCheck: true,
  },
];

const SKIP_FILES = new Set(['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'uv.lock']);
const MAX_BYTES = 2 * 1024 * 1024;

/** Scan text; returns [{ rule, file, line }] (never the secret itself). */
export function scanText(text, file = '', firstLine = 1) {
  const findings = [];
  text.split('\n').forEach((line, i) => {
    if (line.includes('secret-scan:allow')) return;
    for (const rule of RULES) {
      const m = rule.re.exec(line);
      if (!m) continue;
      const value = rule.group ? m[rule.group] : m[0];
      if (rule.placeholderCheck && PLACEHOLDER.test(value)) continue;
      if (!rule.placeholderCheck && /EXAMPLE/.test(m[0])) continue; // documented provider sample keys
      findings.push({ rule: rule.id, file, line: firstLine + i });
      break;
    }
  });
  return findings;
}

function scanBuffer(buf, file) {
  if (buf.length > MAX_BYTES || isBinary(buf)) return [];
  return scanText(buf.toString('utf8'), file);
}

export function scanAll(root) {
  const files = G.isRepo(root) ? G.workingFiles(root) : walk(root);
  const findings = [];
  for (const f of files) {
    if (SKIP_FILES.has(path.posix.basename(f))) continue;
    let buf;
    try {
      buf = fs.readFileSync(path.join(root, f));
    } catch {
      continue;
    }
    findings.push(...scanBuffer(buf, f));
  }
  return findings;
}

export function scanStaged(root) {
  const findings = [];
  for (const f of G.stagedFiles(root)) {
    if (SKIP_FILES.has(path.posix.basename(f))) continue;
    const buf = G.stagedBlob(f, root);
    if (buf) findings.push(...scanBuffer(buf, f));
  }
  return findings;
}

export function scanRange(root, base) {
  const findings = [];
  for (const { path: p, line, text } of G.rangeAddedLines(base, root)) {
    if (SKIP_FILES.has(path.posix.basename(p))) continue;
    findings.push(...scanText(text, p, line));
  }
  return findings;
}

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  let mode = 'all';
  let findings;
  if (argv.includes('--staged')) {
    mode = 'staged';
    findings = scanStaged(root);
  } else if (argv.includes('--range')) {
    mode = 'range';
    findings = scanRange(root, argValue(argv, '--range'));
  } else {
    findings = scanAll(root);
  }

  if (findings.length === 0) {
    console.log(`check-secrets (${mode}): no potential secrets found.`);
    process.exit(EXIT.PASS);
  }
  console.error(`check-secrets (${mode}): ${findings.length} potential secret(s) found:`);
  for (const f of findings) console.error(`  ${f.file}:${f.line}  ${f.rule}`);
  console.error(
    'Values are not printed. If a hit is a false positive, replace the value with an obvious placeholder.\n' +
      'If a real secret was exposed, treat it as compromised and rotate it (governance/SECURITY_POLICY.md section 5).',
  );
  process.exit(EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
