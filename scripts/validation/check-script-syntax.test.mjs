import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { checkScriptSyntax } from './check-script-syntax.mjs';

const created = [];
after(() => created.forEach((d) => fs.rmSync(d, { recursive: true, force: true })));

function fixture(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'syntax-check-'));
  created.push(root);
  for (const [rel, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), content);
  }
  return root;
}

test('script syntax: valid scripts and LF hooks pass', () => {
  const root = fixture({ 'scripts/a.mjs': 'export const a = 1;\n', 'scripts/hooks/pre-commit': '#!/bin/sh\nexit 0\n' });
  assert.deepEqual(checkScriptSyntax(root).errors, []);
});

test('script syntax: a syntax error is reported', () => {
  const root = fixture({ 'scripts/bad.mjs': 'export const = ;\n' });
  const { errors } = checkScriptSyntax(root);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /bad\.mjs/);
});

test('script syntax: a CRLF shell hook is reported', () => {
  const root = fixture({ 'scripts/hooks/pre-commit': '#!/bin/sh\r\nexit 0\r\n' });
  assert.ok(checkScriptSyntax(root).errors.some((e) => e.includes('CRLF')));
});
