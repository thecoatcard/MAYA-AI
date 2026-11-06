import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from '../lib/fsutil.mjs';
import { checkConfigSyntax } from './check-config-syntax.mjs';

const created = [];
const hasYaml = await import('yaml').then(
  () => true,
  () => false,
);

after(() => created.forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })));

function fixture({ files = {}, ignored = [], untracked = {} } = {}) {
  const root = path.join(ROOT, `.check-config-syntax-test-${randomUUID()}`);
  fs.mkdirSync(root);
  created.push(root);
  execFileSync('git', ['init', '--quiet'], { cwd: root, stdio: 'ignore' });
  fs.writeFileSync(path.join(root, '.gitignore'), `${ignored.join('\n')}\n`);

  for (const [rel, content] of Object.entries({ ...files, ...untracked })) {
    const file = path.join(root, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
  }
  execFileSync('git', ['add', '-f', '--', '.gitignore', ...Object.keys(files)], {
    cwd: root,
    stdio: 'ignore',
  });
  return root;
}

test('config syntax: scans tracked configs, not ignored or untracked JSON/YAML', async () => {
  const root = fixture({
    files: {
      'tracked.json': '{"valid":true}\n',
      'configs/tracked.yml': 'valid: true\n',
      '.claude/rules/tracked.md': '---\nname: tracked\n---\nBody\n',
    },
    ignored: ['ignored.json', 'ignored.yml'],
    untracked: {
      'ignored.json': '{"secret":"EXAMPLE_IGNORED_JSON_INPUT"}\n',
      'ignored.yml': 'secret: EXAMPLE_IGNORED_YAML_INPUT\n',
      'untracked.json': '{"secret":"EXAMPLE_UNTRACKED_JSON_INPUT"}\n',
      'untracked.yaml': 'secret: EXAMPLE_UNTRACKED_YAML_INPUT\n',
    },
  });

  const result = await checkConfigSyntax(root);
  assert.deepEqual(result.errors, []);
  assert.equal(result.jsonCount, 1);
  assert.equal(result.yamlCount, hasYaml ? 2 : 0);
  assert.equal(result.yamlSkipped, hasYaml ? 0 : 2);
});

test('config syntax: malformed JSON diagnostics do not disclose input', async () => {
  const root = fixture({
    files: { 'malformed.json': '{"secret":"EXAMPLE_JSON_DIAGNOSTIC_INPUT' },
  });

  const result = await checkConfigSyntax(root);
  assert.deepEqual(result.errors, ['malformed.json: invalid JSON']);
  assert.doesNotMatch(result.errors.join('\n'), /EXAMPLE_JSON_DIAGNOSTIC_INPUT/);
});

test('config syntax: malformed YAML diagnostics do not disclose input', {
  skip: !hasYaml,
}, async () => {
  const root = fixture({
    files: { 'malformed.yml': 'secret: [EXAMPLE_YAML_DIAGNOSTIC_INPUT\n' },
  });

  const result = await checkConfigSyntax(root);
  assert.deepEqual(result.errors, ['malformed.yml: invalid YAML']);
  assert.doesNotMatch(result.errors.join('\n'), /EXAMPLE_YAML_DIAGNOSTIC_INPUT/);
});

test('config syntax: skips tracked symlinks', async (t) => {
  const root = fixture({ files: { 'tracked.json': '{"valid":true}\n' } });
  const target = path.join(root, 'untracked-secret.json');
  fs.writeFileSync(target, '{"secret":"EXAMPLE_SYMLINK_TARGET_INPUT');

  try {
    fs.symlinkSync(target, path.join(root, 'linked.json'), 'file');
  } catch {
    t.skip('file symlink creation is not supported in this environment');
    return;
  }
  execFileSync('git', ['add', '-f', '--', 'linked.json'], { cwd: root, stdio: 'ignore' });

  const result = await checkConfigSyntax(root);
  assert.deepEqual(result.errors, []);
  assert.equal(result.jsonCount, 1);
  assert.doesNotMatch(result.errors.join('\n'), /SYMLINK_TARGET_INPUT/);
});
