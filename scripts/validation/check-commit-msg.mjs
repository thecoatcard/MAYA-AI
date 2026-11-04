// Validates commit messages: Conventional Commits header, and the approval
// trailer when a commit touches guardrail files.
//
// Usage: node scripts/validation/check-commit-msg.mjs <message-file>     (commit-msg hook)
//        node scripts/validation/check-commit-msg.mjs --range <base>    (pre-push / CI)
import fs from 'node:fs';
import { ROOT, EXIT, isMain, argValue } from '../lib/fsutil.mjs';
import { loadConfig } from '../lib/config.mjs';
import { cleanMessage, shouldSkip, validateHeader, hasApproval } from '../lib/commit.mjs';
import { approvalPathsTouched } from './check-sensitive.mjs';
import * as G from '../lib/git.mjs';

/**
 * @param {{ bot?: boolean }} [opts] bot-authored commits (for example Dependabot) cannot add the approval
 *   trailer; a human reviewing and merging that pull request is the approval, enforced by CODEOWNERS and
 *   branch protection rather than by this audit-trail check.
 * @returns {string[]} problems found (empty means the message is acceptable)
 */
export function checkMessage(message, files, cfg, opts = {}) {
  const clean = cleanMessage(message);
  const header = clean.split('\n')[0] ?? '';
  if (header.trim() === '') return ['Commit message is empty.'];
  if (shouldSkip(header, cfg)) return [];

  const errors = [...validateHeader(header, cfg).errors];

  const touched = approvalPathsTouched(files, cfg);
  const trailer = cfg.sensitive.approvalTrailer;
  if (touched.length > 0 && !opts.bot && !hasApproval(clean, trailer)) {
    const shown = touched.slice(0, 5).join(', ') + (touched.length > 5 ? `, … (+${touched.length - 5} more)` : '');
    errors.push(
      `This commit changes guardrail files (${shown}). Add a trailer such as "${trailer}: <who approved, and where>" ` +
        'as the last paragraph of the message (governance/GIT_WORKFLOW.md).',
    );
  }
  return errors;
}

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  const cfg = loadConfig(root);
  let failed = 0;

  if (argv.includes('--range')) {
    const base = argValue(argv, '--range');
    for (const c of G.rangeCommits(base, root)) {
      const errors = checkMessage(c.message, c.files, cfg, { bot: /\[bot\]$/i.test(c.author ?? '') });
      if (errors.length > 0) {
        failed += 1;
        console.error(`commit ${c.sha.slice(0, 7)}:`);
        for (const e of errors) console.error(`  - ${e}`);
      }
    }
    if (failed === 0) console.log(`check-commit-msg (range ${base}): all commit messages are valid.`);
  } else {
    const file = argv.find((a) => !a.startsWith('--'));
    if (!file) {
      console.error('usage: check-commit-msg.mjs <message-file> | --range <base>');
      process.exit(EXIT.FAIL);
    }
    const errors = checkMessage(fs.readFileSync(file, 'utf8'), G.stagedFiles(root), cfg);
    if (errors.length > 0) {
      failed = 1;
      console.error('Commit message rejected:');
      for (const e of errors) console.error(`  - ${e}`);
      console.error('See governance/GIT_WORKFLOW.md for the convention and trailers.');
    }
  }
  process.exit(failed === 0 ? EXIT.PASS : EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
