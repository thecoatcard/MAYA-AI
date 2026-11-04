import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildExpected, checkAgentFiles, extractHardRules } from './sync-agent-files.mjs';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'sync-agent-files.mjs');
const MARKERS = ['<!-- BEGIN:hard-rules -->', '<!-- END:hard-rules -->'];
const CONFIG = {
  instructionFiles: { files: ['AGENTS.md'], maxBytes: 500, warnBytes: 400 },
  agentWrappers: { prefix: 'gov-', rolePrefix: 'role-', hardRulesMarkers: MARKERS },
};

const created = [];
after(() => created.forEach((d) => fs.rmSync(d, { recursive: true, force: true })));

function fixture(extra = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agents-sync-'));
  created.push(root);
  const files = {
    'governance/governance.config.json': JSON.stringify(CONFIG),
    'AGENTS.md': `# Agents\n${MARKERS[0]}\n## Hard rules\n1. Be careful.\n${MARKERS[1]}\n`,
    'CLAUDE.md': '@AGENTS.md\n',
    'GEMINI.md': '@./AGENTS.md\n',
    'governance/workflows/plan-feature.md': '---\nname: plan-feature\ndescription: Plan a feature safely.\n---\n# Plan\n',
    'governance/roles/code-reviewer.md': '---\nname: code-reviewer\ndescription: Reviews code.\nclaude_tools: Read, Grep\n---\n# Role\n',
    ...extra,
  };
  for (const [rel, content] of Object.entries(files)) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  }
  return root;
}
const cli = (root, ...args) => spawnSync(process.execPath, [SCRIPT, '--root', root, ...args], { encoding: 'utf8' });

test('sync: extracts the hard-rules block and rejects a missing block', () => {
  assert.match(extractHardRules(`a\n${MARKERS[0]}\nRULES\n${MARKERS[1]}\n`, MARKERS), /^RULES$/);
  assert.equal(extractHardRules('no markers', MARKERS), null);
});

test('sync: expected outputs cover every tool and point at the canonical files', () => {
  const { files, problems } = buildExpected(fixture());
  assert.deepEqual(problems, []);
  for (const rel of [
    '.claude/skills/gov-plan-feature/SKILL.md',
    '.agents/skills/gov-plan-feature/SKILL.md',
    '.github/prompts/gov-plan-feature.prompt.md',
    '.cursor/rules/gov-plan-feature.mdc',
    '.claude/agents/role-code-reviewer.md',
    '.github/copilot-instructions.md',
    '.cursor/rules/maya-governance.mdc',
  ]) {
    assert.ok(files.has(rel), `missing ${rel}`);
  }
  assert.match(files.get('.claude/skills/gov-plan-feature/SKILL.md'), /governance\/workflows\/plan-feature\.md/);
  assert.match(files.get('.claude/skills/gov-plan-feature/SKILL.md'), /disable-model-invocation: true/);
  assert.match(files.get('.claude/agents/role-code-reviewer.md'), /tools: Read, Grep/);
  assert.match(files.get('.github/copilot-instructions.md'), /1\. Be careful\./);
});

test('sync: write then check passes; drift, stale files and missing entry points are detected', () => {
  const root = fixture();
  assert.equal(cli(root).status, 0);
  assert.equal(cli(root, '--check').status, 0);

  const skill = path.join(root, '.claude/skills/gov-plan-feature/SKILL.md');
  fs.appendFileSync(skill, 'tampered\n');
  const drift = cli(root, '--check');
  assert.equal(drift.status, 1);
  assert.match(drift.stderr, /out of date/);

  assert.equal(cli(root).status, 0, 'sync repairs drift');
  assert.equal(cli(root, '--check').status, 0);

  fs.mkdirSync(path.join(root, '.agents/skills/gov-removed'), { recursive: true });
  fs.writeFileSync(path.join(root, '.agents/skills/gov-removed/SKILL.md'), 'stale');
  assert.match(cli(root, '--check').stderr, /stale generated file/);
  assert.equal(cli(root).status, 0);
  assert.equal(fs.existsSync(path.join(root, '.agents/skills/gov-removed')), false, 'stale output is removed');

  fs.writeFileSync(path.join(root, 'CLAUDE.md'), 'no import here\n');
  assert.match(cli(root, '--check').stderr, /CLAUDE\.md must import/);
});

test('sync: a workflow whose front matter name does not match its file name is a problem', () => {
  const root = fixture({ 'governance/workflows/bad.md': '---\nname: other\ndescription: x\n---\n' });
  assert.ok(checkAgentFiles(root).errors.some((e) => e.includes('must equal the file name')));
});

test('sync: AGENTS.md over the size limit is reported', () => {
  const root = fixture();
  fs.appendFileSync(path.join(root, 'AGENTS.md'), 'x'.repeat(600));
  cli(root);
  assert.ok(checkAgentFiles(root).errors.some((e) => e.includes('the limit is 500')));
});
