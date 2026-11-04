import test from 'node:test';
import assert from 'node:assert/strict';
import { scanText } from './check-secrets.mjs';
import { isBlocked, approvalPathsTouched } from './check-sensitive.mjs';
import { checkMessage } from './check-commit-msg.mjs';
import { evaluate } from './check-change-rules.mjs';
import { lintWorkflow } from './check-workflows.mjs';
import { planChecks } from './run-stack-checks.mjs';

// Fake credentials are assembled at runtime so this file itself stays clean for the scanner.
const FAKE = {
  aws: 'AKIA' + 'ABCDEFGHIJKLMNOP',
  gh: 'ghp_' + 'a1B2c3D4e5'.repeat(4),
  key: '-----BEGIN ' + 'RSA PRIVATE KEY-----',
  jwt: 'eyJ' + 'a'.repeat(12) + '.eyJ' + 'b'.repeat(12) + '.' + 'c'.repeat(12),
  slack: 'xoxb' + '-1234567890-abcdefghij',
};

const cfg = {
  commits: { types: ['feat', 'fix', 'docs', 'test', 'chore', 'security', 'style'], maxHeaderLength: 100, skipPrefixes: ['Merge '] },
  sensitive: {
    blockedFiles: ['.env', '.env.*', '*.pem', 'secrets/**'],
    blockedExceptions: ['.env.example'],
    approvalRequiredPaths: ['governance/**', '.github/workflows/**', 'package.json'],
    approvalTrailer: 'Human-Approval',
  },
  changeRules: {
    ignoreChangedPaths: ['docs/**', '**/*.md', '**/tests/**'],
    exemptCommitTypes: ['style', 'test', 'docs'],
    docRules: [
      { id: 'api', description: 'API changes need docs', when: ['backend/app/api/**'], requireOneOf: ['docs/api/**'], trailer: 'Docs' },
    ],
    adrRule: {
      id: 'architecture',
      description: 'needs ADR',
      when: ['backend/app/agents/**'],
      requireOneOf: ['docs/adr/*.md'],
      excludeFiles: ['docs/adr/README.md'],
      commitTypes: ['feat', 'refactor'],
      trailer: 'ADR',
    },
    changelog: { file: 'CHANGELOG.md', requiredForCommitTypes: ['feat', 'fix'], trailer: 'Changelog' },
  },
  stacks: [
    { name: 'py', detect: 'backend/pyproject.toml', cwd: 'backend', lint: [['ruff', 'check', '.']], typecheck: [['mypy', '.']], test: [['pytest']], build: [] },
  ],
};

test('secrets: detects each credential family without echoing the value', () => {
  for (const [name, value] of Object.entries(FAKE)) {
    const hits = scanText(`config = ${name === 'key' ? value : `"${value}"`}`, 'f.txt');
    assert.equal(hits.length, 1, `expected a hit for ${name}`);
    assert.ok(!JSON.stringify(hits).includes(value), 'finding must not contain the secret value');
  }
});

test('secrets: placeholders, provider sample keys and allow markers are not flagged', () => {
  assert.equal(scanText('API_KEY=your-api-key-here', '.env.example').length, 0);
  assert.equal(scanText('password: "changeme-please-1234"', 'x.yml').length, 0);
  assert.equal(scanText('postgresql://user:password@localhost/db', 'x.md').length, 0);
  assert.equal(scanText('key AKIAIOSFODNN7EXAMPLE in the AWS docs', 'x.md').length, 0);
  assert.equal(scanText(`x = ${FAKE.aws} # secret-scan:allow`, 'x.txt').length, 0);
});

test('secrets: real-looking assignments and connection strings are flagged', () => {
  const value = 'Zq8vN3kP0wXr7LmT';
  assert.equal(scanText(`api_key = "${value}"`, 'a.py').length, 1);
  assert.equal(scanText(`DB=postgres://admin:${'Sup3rS3cret' + 'Pw'}@db.internal/app`, 'a.env').length, 1);
  assert.equal(scanText(`STRIPE_SECRET_KEY=${value}9`, '.env').length, 1);
});

test('sensitive: blocks env/key files but allows .env.example', () => {
  assert.equal(isBlocked('.env', cfg), true);
  assert.equal(isBlocked('apps/web/.env.local', cfg), true);
  assert.equal(isBlocked('certs/server.pem', cfg), true);
  assert.equal(isBlocked('secrets/token.txt', cfg), true);
  assert.equal(isBlocked('.env.example', cfg), false);
  assert.equal(isBlocked('src/env.ts', cfg), false);
});

test('sensitive: identifies guardrail paths', () => {
  assert.deepEqual(approvalPathsTouched(['src/a.ts', 'governance/README.md', 'package.json'], cfg), ['governance/README.md', 'package.json']);
});

test('commit message: header and approval trailer rules', () => {
  assert.deepEqual(checkMessage('feat: add thing', ['src/a.ts'], cfg), []);
  assert.ok(checkMessage('add thing', ['src/a.ts'], cfg).length > 0);
  assert.ok(checkMessage('', ['src/a.ts'], cfg).length > 0);
  assert.deepEqual(checkMessage('Merge branch main', ['governance/x.md'], cfg), []);
  assert.ok(checkMessage('docs: edit policy', ['governance/README.md'], cfg).some((e) => e.includes('Human-Approval')));
  assert.deepEqual(checkMessage('docs: edit policy\n\nHuman-Approval: owner approved in session', ['governance/README.md'], cfg), []);
});

test('commit message: bot-authored commits skip the approval trailer but not the header rule', () => {
  assert.deepEqual(checkMessage('chore: bump actions', ['.github/workflows/x.yml'], cfg, { bot: true }), []);
  assert.ok(checkMessage('Bump actions', ['.github/workflows/x.yml'], cfg, { bot: true }).length > 0);
});

test('change rules: API change without docs fails; docs or a specific waiver satisfy it', () => {
  const commits = [{ message: 'feat: new endpoint' }];
  const changelog = { status: 'M', path: 'CHANGELOG.md' };
  const api = { status: 'A', path: 'backend/app/api/v1/tasks.py' };

  assert.ok(evaluate({ files: [api, changelog], commits, cfg }).errors.some((e) => e.includes('[api]')));
  assert.equal(evaluate({ files: [api, changelog, { status: 'M', path: 'docs/api/tasks.md' }], commits, cfg }).errors.length, 0);
  const waived = [{ message: 'feat: new endpoint\n\nDocs: not-required: endpoint is internal and undocumented by design' }];
  const r = evaluate({ files: [api, changelog], commits: waived, cfg });
  assert.equal(r.errors.length, 0);
  assert.equal(r.waived.length, 1);
  const vague = [{ message: 'feat: new endpoint\n\nDocs: not-required: n/a' }];
  assert.ok(evaluate({ files: [api, changelog], commits: vague, cfg }).errors.length > 0);
});

test('change rules: ADR and changelog requirements', () => {
  const agent = { status: 'M', path: 'backend/app/agents/orchestrator.py' };
  const doc = { status: 'M', path: 'docs/modules/agents.md' };
  const feat = [{ message: 'feat: change orchestration' }];

  const noAdr = evaluate({ files: [agent, doc, { status: 'M', path: 'CHANGELOG.md' }], commits: feat, cfg });
  assert.ok(noAdr.errors.some((e) => e.includes('[architecture]')));
  const withAdr = evaluate({ files: [agent, doc, { status: 'A', path: 'docs/adr/0003-x.md' }, { status: 'M', path: 'CHANGELOG.md' }], commits: feat, cfg });
  assert.equal(withAdr.errors.length, 0);
  const readmeOnly = evaluate({ files: [agent, { status: 'M', path: 'docs/adr/README.md' }, { status: 'M', path: 'CHANGELOG.md' }], commits: feat, cfg });
  assert.ok(readmeOnly.errors.some((e) => e.includes('[architecture]')), 'the ADR index alone is not an ADR');
  assert.ok(evaluate({ files: [{ status: 'M', path: 'src/x.ts' }], commits: [{ message: 'fix: bug' }], cfg }).errors.some((e) => e.includes('[changelog]')));
});

test('change rules: style-only commits and test-only paths do not trigger documentation rules', () => {
  const api = { status: 'M', path: 'backend/app/api/v1/tasks.py' };
  assert.equal(evaluate({ files: [api], commits: [{ message: 'style: format' }], cfg }).errors.length, 0);
  assert.equal(evaluate({ files: [{ status: 'M', path: 'backend/app/api/tests/test_x.py' }], commits: [{ message: 'chore: tweak' }], cfg }).errors.length, 0);
});

test('workflow lint: flags unpinned actions, missing permissions, risky triggers and injection', () => {
  const sha = 'a'.repeat(40);
  const good = {
    on: { pull_request: {} },
    permissions: { contents: 'read' },
    jobs: { j: { 'timeout-minutes': 5, steps: [{ uses: `actions/checkout@${sha}` }, { run: 'echo hi' }] } },
  };
  assert.deepEqual(lintWorkflow('w.yml', good), []);

  const unpinned = structuredClone(good);
  unpinned.jobs.j.steps[0].uses = 'actions/checkout@v4';
  assert.ok(lintWorkflow('w.yml', unpinned).some((e) => e.includes('not pinned')));

  const noPerms = structuredClone(good);
  delete noPerms.permissions;
  assert.ok(lintWorkflow('w.yml', noPerms).some((e) => e.includes('permissions')));

  const target = structuredClone(good);
  target.on = { pull_request_target: {} };
  assert.ok(lintWorkflow('w.yml', target).some((e) => e.includes('pull_request_target')));

  const inject = structuredClone(good);
  inject.jobs.j.steps[1].run = 'echo ${{ github.event.pull_request.title }}';
  assert.ok(lintWorkflow('w.yml', inject).some((e) => e.includes('untrusted')));

  const noTimeout = structuredClone(good);
  delete noTimeout.jobs.j['timeout-minutes'];
  assert.ok(lintWorkflow('w.yml', noTimeout).some((e) => e.includes('timeout')));
});

test('stack checks: nothing is planned (so the result is SKIPPED) when no project exists', () => {
  const none = planChecks('/definitely/not/a/real/root', cfg, ['lint']);
  assert.deepEqual(none.detected, []);
  assert.deepEqual(none.plan, []);
});
