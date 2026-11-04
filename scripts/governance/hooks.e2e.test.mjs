// End-to-end tests for the Git hooks. Each test builds a throwaway repository in the OS
// temp directory, copies the real scripts into it (without their tests), installs the hooks
// with the real setup script, and then drives real "git commit" and "git push" commands.
// The repository under test is never touched.
//
// Not covered on purpose: bypassing hooks with --no-verify. That is documented Git behavior
// (governance/GIT_WORKFLOW.md) and is exactly what CI and branch protection exist to catch.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const MARKERS = ['<!-- BEGIN:hard-rules -->', '<!-- END:hard-rules -->'];

// Every throwaway directory is removed when the file's tests finish.
const created = [];
after(() => {
  for (const dir of created) fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
});
const tempDir = (prefix) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  created.push(dir);
  return dir;
};

const run = (cwd, cmd, args) => spawnSync(cmd, args, { cwd, encoding: 'utf8' });
const git = (cwd, ...args) => run(cwd, 'git', args);
const output = (r) => `${r.stdout ?? ''}${r.stderr ?? ''}`;
const write = (root, rel, content) => {
  fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
  fs.writeFileSync(path.join(root, rel), content);
};

/** A fresh repository with the real scripts and hooks installed, and one baseline commit. */
function makeRepo() {
  const root = tempDir('hooks-e2e-');
  fs.cpSync(path.join(REPO, 'scripts'), path.join(root, 'scripts'), {
    recursive: true,
    filter: (src) => !src.endsWith('.test.mjs') && !src.includes('node_modules'),
  });

  const config = JSON.parse(fs.readFileSync(path.join(REPO, 'governance', 'governance.config.json'), 'utf8'));
  config.requiredFiles = [];
  write(root, 'governance/governance.config.json', JSON.stringify(config, null, 2));
  write(root, 'AGENTS.md', `# Agents\n${MARKERS[0]}\n## Hard rules\n1. Be careful.\n${MARKERS[1]}\n`);
  write(root, 'governance/README.md', '# Governance\n'); // referenced by the generated Copilot instructions
  write(root, 'CLAUDE.md', '@AGENTS.md\n');
  write(root, 'GEMINI.md', '@./AGENTS.md\n');
  write(root, 'package.json', '{ "name": "fixture", "private": true, "type": "module" }\n');

  assert.equal(git(root, 'init', '-q', '-b', 'main').status, 0);
  git(root, 'config', 'user.name', 'Hook Tester');
  git(root, 'config', 'user.email', 'tester@example.com');
  git(root, 'config', 'core.autocrlf', 'false');

  assert.equal(run(root, process.execPath, ['scripts/governance/sync-agent-files.mjs']).status, 0);
  git(root, 'add', '-A');
  // Hooks are not installed yet, so the baseline commit is not subject to them.
  assert.equal(git(root, 'commit', '-q', '-m', 'chore: baseline').status, 0, 'baseline commit');

  const setup = run(root, process.execPath, ['scripts/governance/setup.mjs']);
  assert.equal(setup.status, 0, `setup failed:\n${output(setup)}`);
  assert.equal(git(root, 'config', '--get', 'core.hooksPath').stdout.trim(), 'scripts/hooks', 'setup installs the hooks');
  return root;
}

function commit(root, message, ...more) {
  git(root, 'add', '-A');
  const args = ['commit', '-q', '-m', message];
  for (const m of more) args.push('-m', m);
  return git(root, ...args);
}

test('commit-msg hook rejects a message that is not a Conventional Commit', () => {
  const root = makeRepo();
  write(root, 'docs/note.md', '# Note\n');
  const r = commit(root, 'added stuff');
  assert.notEqual(r.status, 0);
  assert.match(output(r), /Header must look like/);
});

test('commit-msg hook requires a Human-Approval trailer for guardrail files, and accepts it when present', () => {
  const root = makeRepo();
  write(root, 'governance/note.md', '# Note\n');
  const missing = commit(root, 'docs: add governance note');
  assert.notEqual(missing.status, 0);
  assert.match(output(missing), /Human-Approval/);

  const ok = commit(root, 'docs: add governance note', 'Human-Approval: owner approved in the test');
  assert.equal(ok.status, 0, output(ok));
  assert.match(git(root, 'log', '-1', '--format=%B').stdout, /Human-Approval: owner approved in the test/);
});

test('pre-commit hook blocks a force-added .env file', () => {
  const root = makeRepo();
  write(root, '.env', 'DB_PASSWORD=not-a-real-value\n');
  git(root, 'add', '-f', '.env');
  const r = git(root, 'commit', '-q', '-m', 'chore: add env file');
  assert.notEqual(r.status, 0);
  assert.match(output(r), /must not be committed/);
});

test('pre-commit hook blocks a committed credential and never prints it', () => {
  const root = makeRepo();
  const token = 'ghp_' + 'a1B2c3D4e5'.repeat(4);
  write(root, 'src/client.ts', `export const t = "${token}";\n`);
  const r = commit(root, 'feat: add client');
  assert.notEqual(r.status, 0);
  assert.match(output(r), /github-token/);
  assert.ok(!output(r).includes(token), 'the secret value must not appear in hook output');
});

test('pre-commit hook blocks a broken documentation link', () => {
  const root = makeRepo();
  write(root, 'docs/guide.md', '# Guide\n[missing](nope.md)\n');
  const r = commit(root, 'docs: add guide');
  assert.notEqual(r.status, 0);
  assert.match(output(r), /broken link/);
});

test('pre-push hook enforces documentation and changelog rules against the base branch', () => {
  const root = makeRepo();
  const remote = tempDir('hooks-e2e-remote-');
  assert.equal(git(remote, 'init', '-q', '--bare').status, 0);
  git(root, 'remote', 'add', 'origin', remote);
  assert.equal(git(root, 'push', '-q', 'origin', 'main').status, 0, 'pushing the baseline passes (nothing to compare)');

  git(root, 'checkout', '-q', '-b', 'feat/endpoint');
  write(root, 'backend/app/api/README.md', '# api\nHTTP endpoints.\n'); // new modules need documentation (pre-commit rule)
  write(root, 'backend/app/api/v1/tasks.py', 'def handler():\n    return 1\n');
  const c1 = commit(root, 'feat: add tasks endpoint');
  assert.equal(c1.status, 0, `pre-commit should allow this commit:\n${output(c1)}`);

  const rejected = git(root, 'push', '-q', '-u', 'origin', 'feat/endpoint');
  assert.notEqual(rejected.status, 0);
  assert.match(output(rejected), /\[api\]/);
  assert.match(output(rejected), /\[changelog\]/);

  write(root, 'docs/api/tasks.md', '# Tasks API\nThe tasks endpoint returns a number.\n');
  write(root, 'CHANGELOG.md', '# Changelog\n## [Unreleased]\n- Added the tasks endpoint.\n');
  assert.equal(commit(root, 'docs: document tasks endpoint').status, 0);
  const accepted = git(root, 'push', '-q', '-u', 'origin', 'feat/endpoint');
  assert.equal(accepted.status, 0, output(accepted));
});

test('pre-push hook accepts a specific waiver trailer and rejects a vague one', () => {
  const root = makeRepo();
  const remote = tempDir('hooks-e2e-remote-');
  git(remote, 'init', '-q', '--bare');
  git(root, 'remote', 'add', 'origin', remote);
  assert.equal(git(root, 'push', '-q', 'origin', 'main').status, 0);

  git(root, 'checkout', '-q', '-b', 'fix/internal');
  write(root, 'backend/app/api/README.md', '# api\nHTTP endpoints.\n');
  write(root, 'backend/app/api/v1/health.py', 'def ok():\n    return True\n');

  assert.equal(
    commit(root, 'fix: internal health probe', 'Docs: not-required: n/a', 'Changelog: not-required: n/a').status,
    0,
  );
  const vague = git(root, 'push', '-q', '-u', 'origin', 'fix/internal');
  assert.notEqual(vague.status, 0, 'a vague waiver must not satisfy the rules');

  git(root, 'commit', '-q', '--amend', '-m', 'fix: internal health probe',
    '-m', 'Docs: not-required: internal liveness probe, not part of the public API\nChangelog: not-required: no user-visible effect');
  const ok = git(root, 'push', '-q', '-u', 'origin', 'fix/internal');
  assert.equal(ok.status, 0, output(ok));
});
