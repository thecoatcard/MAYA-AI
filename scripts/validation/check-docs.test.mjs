import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { checkDocs } from './check-docs.mjs';
import { git } from '../lib/git.mjs';

const CONFIG = {
  requiredFiles: ['README.md'],
  markdown: {
    roots: ['README.md', 'docs'],
    ignore: ['templates/**'],
    placeholderPatterns: [{ pattern: '\\bTBD\\b' }, { pattern: 'lorem ipsum', flags: 'i' }],
  },
  modules: { roots: ['backend/app/*'] },
  env: {
    exampleFile: '.env.example',
    referenceDoc: 'docs/CONFIG.md',
    sourceGlobs: ['**/*.ts', '**/*.py'],
    excludeGlobs: ['node_modules/**'],
    ignoredNames: ['CI'],
  },
};

const created = [];
after(() => created.forEach((d) => fs.rmSync(d, { recursive: true, force: true })));

function fixture(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-check-'));
  created.push(root);
  const all = { 'governance/governance.config.json': JSON.stringify(CONFIG), ...files };
  for (const [rel, content] of Object.entries(all)) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  }
  return root;
}
const errors = (root) => checkDocs(root).filter((f) => f.severity === 'error').map((f) => `${f.file}: ${f.message}`);

test('docs: a consistent repository passes', () => {
  const root = fixture({
    'README.md': '# Project\nSee [config](docs/CONFIG.md#settings) and `docs/CONFIG.md`.\n',
    'docs/CONFIG.md': '# Config\n## Settings\nNo variables.\n',
  });
  assert.deepEqual(errors(root), []);
});

test('docs: missing required file, broken link, broken anchor and missing referenced path are errors', () => {
  const root = fixture({
    'docs/CONFIG.md': '# Config\n[gone](missing.md)\n[anchor](#nope)\nSee `docs/not-there.md`.\n',
  });
  const e = errors(root).join('\n');
  assert.match(e, /README\.md: required file is missing/);
  assert.match(e, /broken link "missing\.md"/);
  assert.match(e, /broken anchor "#nope"/);
  assert.match(e, /`docs\/not-there\.md` refers to a file that does not exist/);
});

test('docs: placeholder text is rejected in prose but allowed in code and templates', () => {
  const root = fixture({
    'README.md': '# P\nThis part is TBD.\n`TBD` in code is fine.\n\n```\nTBD in a fence is fine\n```\n',
    'docs/CONFIG.md': '# C\n',
    'templates/t.md': 'TBD lorem ipsum\n',
  });
  const e = errors(root);
  assert.equal(e.length, 1);
  assert.match(e[0], /README\.md/);
});

test('docs: environment variables used in source must be in .env.example and the reference doc', () => {
  const base = { 'README.md': '# P\n', 'docs/CONFIG.md': '# C\n`DATABASE_URL`\n' };
  const missing = fixture({ ...base, 'src/app.ts': 'const u = process.env.DATABASE_URL;\n', '.env.example': '' });
  assert.match(errors(missing).join('\n'), /DATABASE_URL is used but missing from \.env\.example/);

  const ok = fixture({ ...base, 'src/app.ts': 'const u = process.env.DATABASE_URL;\n', '.env.example': 'DATABASE_URL=postgresql://user:password@localhost/db\n' });
  assert.deepEqual(errors(ok), []);

  const undocumented = fixture({ 'README.md': '# P\n', 'docs/CONFIG.md': '# C\n', 'src/a.py': 'import os\nx = os.getenv("API_BASE")\n', '.env.example': 'API_BASE=http://localhost\n' });
  assert.match(errors(undocumented).join('\n'), /API_BASE is used but missing from docs\/CONFIG\.md/);

  const ignored = fixture({ ...base, '.env.example': '', 'docs/CONFIG.md': '# C\n', 'src/c.ts': 'if (process.env.CI) {}\n' });
  assert.deepEqual(errors(ignored), []);
});

test('docs: new modules need a README or a docs/modules page', () => {
  const files = { 'README.md': '# P\n', 'docs/CONFIG.md': '# C\n', 'backend/app/mcp/x.py': '' };
  assert.match(errors(fixture(files)).join('\n'), /backend\/app\/mcp: module has no documentation/);
  assert.deepEqual(errors(fixture({ ...files, 'backend/app/mcp/README.md': '# mcp\n' })), []);
  assert.deepEqual(errors(fixture({ ...files, 'docs/modules/mcp.md': '# mcp\n' })), []);
});

test('docs: ignored build and dependency files are not application env sources', () => {
  const root = fixture({
    'README.md': '# P\n',
    'docs/CONFIG.md': '# C\n',
    '.gitignore': '**/.next/\n**/.venv/\n',
    'apps/web/.next/server/generated.ts': 'process.env.BUILD_INTERNAL;\n',
    'backend/.venv/lib/dependency.py': 'import os\nos.getenv("LIBRARY_INTERNAL")\n',
    'src/local.ts': 'process.env.REQUIRED_SOURCE;\n',
  });
  git(['init', '-q'], { cwd: root });
  const findings = errors(root).join('\n');
  assert.doesNotMatch(findings, /BUILD_INTERNAL|LIBRARY_INTERNAL/);
  assert.match(findings, /REQUIRED_SOURCE is used but missing/);
});

test('docs: tracked source is still scanned even when an ignore pattern matches it', () => {
  const root = fixture({
    'README.md': '# P\n',
    'docs/CONFIG.md': '# C\n',
    '.gitignore': 'src/\n',
    'src/tracked.ts': 'process.env.TRACKED_SOURCE;\n',
  });
  git(['init', '-q'], { cwd: root });
  git(['add', '-f', 'src/tracked.ts'], { cwd: root });
  assert.match(errors(root).join('\n'), /TRACKED_SOURCE is used but missing/);
});
