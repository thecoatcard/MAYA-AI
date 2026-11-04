import test from 'node:test';
import assert from 'node:assert/strict';
import { matchGlob, matchesAny } from './glob.mjs';
import { parseFrontmatter } from './frontmatter.mjs';
import { analyze, slugify } from './markdown.mjs';
import { validateHeader, parseTrailers, parseWaiver, findWaiver, hasApproval, cleanMessage } from './commit.mjs';

const cfg = {
  commits: { types: ['feat', 'fix', 'docs', 'chore', 'security'], maxHeaderLength: 72, skipPrefixes: ['Merge '] },
};

test('glob: ** matches any depth, including the root level', () => {
  assert.equal(matchGlob('docs/a/b/c.md', 'docs/**'), true);
  assert.equal(matchGlob('x/y/migrations/001.py', '**/migrations/**'), true);
  assert.equal(matchGlob('Dockerfile', '**/Dockerfile'), true);
  assert.equal(matchGlob('docs/a.md', 'src/**'), false);
});

test('glob: * stays within one path segment', () => {
  assert.equal(matchGlob('docs/adr/0001-x.md', 'docs/adr/*.md'), true);
  assert.equal(matchGlob('docs/adr/sub/0001-x.md', 'docs/adr/*.md'), false);
});

test('glob: a pattern without a slash matches the basename at any depth', () => {
  assert.equal(matchGlob('a/b/.env', '.env'), true);
  assert.equal(matchGlob('a/b/key.pem', '*.pem'), true);
  assert.equal(matchGlob('a/b/.env.example', '.env'), false);
  assert.equal(matchesAny('.env.local', ['.env.*']), true);
});

test('frontmatter: parses flat keys and JSON-quoted values', () => {
  const { data, body } = parseFrontmatter('---\nname: x\ndescription: "a: b, c"\n---\nBody\n');
  assert.equal(data.name, 'x');
  assert.equal(data.description, 'a: b, c');
  assert.equal(body, 'Body\n');
  assert.deepEqual(parseFrontmatter('no front matter').data, {});
});

test('markdown: code fences, inline code and comments are not treated as prose', () => {
  const md = ['# Title', 'Real TBD text', '```', 'TBD in fence', '```', 'See `inline TBD` here', '<!-- TBD comment -->', '[ok](a.md#title)'].join('\n');
  const a = analyze(md);
  const prose = a.lines.join('\n');
  assert.match(prose, /Real TBD text/);
  assert.doesNotMatch(prose, /fence/);
  assert.doesNotMatch(prose, /inline TBD/);
  assert.doesNotMatch(prose, /comment/);
  assert.deepEqual(a.inlineCodes.map((c) => c.text), ['inline TBD']);
  assert.equal(a.links[0].href, 'a.md#title');
});

test('markdown: heading slugs match GitHub and de-duplicate', () => {
  assert.equal(slugify('Hello, World! (v2)'), 'hello-world-v2');
  const a = analyze('# Same\n## Same\n## `Code` Heading');
  assert.deepEqual(a.headings.map((h) => h.slug), ['same', 'same-1', 'code-heading']);
});

test('commit: accepts valid headers and rejects invalid ones', () => {
  assert.equal(validateHeader('feat(mcp): add pool', cfg).ok, true);
  assert.equal(validateHeader('fix!: drop legacy field', cfg).ok, true);
  assert.equal(validateHeader('added stuff', cfg).ok, false);
  assert.equal(validateHeader('feat:no space', cfg).ok, false);
  assert.equal(validateHeader('wip: thing', cfg).ok, false);
  assert.equal(validateHeader(`feat: ${'x'.repeat(80)}`, cfg).ok, false);
});

test('commit: trailers are read from the final paragraph only', () => {
  const msg = 'feat: x\n\nBody text.\n\nDocs: not-required: internal rename with no interface change\nHuman-Approval: owner in review';
  const t = parseTrailers(msg);
  assert.equal(t.get('human-approval')[0], 'owner in review');
  assert.equal(parseTrailers('feat: x\n\nA sentence: with a colon in prose.').size, 0);
  assert.equal(parseTrailers('feat: x').size, 0);
});

test('commit: waivers need a specific reason', () => {
  assert.equal(parseWaiver('not-required: internal rename with no interface change').valid, true);
  assert.equal(parseWaiver('not-required: n/a').valid, false);
  assert.equal(parseWaiver('not-required: tbd').valid, false);
  assert.equal(parseWaiver('yes').valid, false);
  assert.equal(findWaiver(['feat: x\n\nDocs: not-required: n/a'], 'Docs'), null);
  assert.ok(findWaiver(['feat: x\n\nDocs: not-required: formatting only, no content change'], 'Docs'));
});

test('commit: approval trailer must have a value; git comment lines are ignored', () => {
  assert.equal(hasApproval('feat: x\n\nHuman-Approval: owner', 'Human-Approval'), true);
  assert.equal(hasApproval('feat: x', 'Human-Approval'), false);
  assert.equal(cleanMessage('feat: x\n# a git comment\n'), 'feat: x');
});
