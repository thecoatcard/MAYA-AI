// Documentation validation.
//   1. Required files exist.
//   2. Internal Markdown links (and heading anchors) resolve.
//   3. Repository paths written in inline code point at files that exist.
//   4. No placeholder / unfinished-work text outside code and templates.
//   5. Environment variables used by application source are in .env.example and the configuration reference.
//   6. Module directories under the configured roots have a README or a docs/modules page.
//
// Usage: node scripts/validation/check-docs.mjs [--root <dir>]
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, EXIT, walk, exists, isDirectory, readText, listDirs, isMain, argValue } from '../lib/fsutil.mjs';
import { loadConfig } from '../lib/config.mjs';
import { analyze } from '../lib/markdown.mjs';
import { matchesAny } from '../lib/glob.mjs';

const PATH_TOKEN = /^(?:\.\/)?[A-Za-z0-9_.@-]+(?:\/[A-Za-z0-9_.@-]+)+$/;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

export function collectMarkdown(root, cfg) {
  const files = new Set();
  for (const entry of cfg.markdown.roots) {
    if (!exists(root, entry)) continue;
    if (isDirectory(root, entry)) {
      for (const f of walk(root, entry)) if (f.endsWith('.md')) files.add(f);
    } else if (entry.endsWith('.md')) {
      files.add(entry);
    }
  }
  return [...files].filter((f) => !matchesAny(f, cfg.markdown.ignore ?? [])).sort();
}

function topLevelNames(root) {
  return new Set(fs.readdirSync(root).filter((n) => n !== 'node_modules' && n !== '.git'));
}

function checkLink(root, file, link, anchorCache, add) {
  const href = link.href;
  if (SCHEME.test(href) || href.startsWith('<')) return;

  let target = href;
  let anchor = '';
  const hash = href.indexOf('#');
  if (hash !== -1) {
    target = href.slice(0, hash);
    anchor = href.slice(hash + 1);
  }
  try {
    target = decodeURIComponent(target);
  } catch {
    /* keep raw */
  }

  let rel;
  if (target === '') rel = file;
  else if (target.startsWith('/')) rel = path.posix.normalize(target.slice(1));
  else rel = path.posix.normalize(path.posix.join(path.posix.dirname(file), target));

  if (rel.startsWith('..')) {
    add('error', file, link.line, `link "${href}" points outside the repository`);
    return;
  }
  if (!exists(root, rel)) {
    add('error', file, link.line, `broken link "${href}" (no such file: ${rel})`);
    return;
  }
  if (anchor && rel.endsWith('.md') && !isDirectory(root, rel)) {
    if (!anchorCache.has(rel)) {
      anchorCache.set(rel, new Set(analyze(readText(root, rel)).headings.map((h) => h.slug)));
    }
    if (!anchorCache.get(rel).has(anchor.toLowerCase())) {
      add('error', file, link.line, `broken anchor "#${anchor}" in ${rel}`);
    }
  }
}

function checkInlinePaths(root, file, inlineCodes, tops, add) {
  for (const code of inlineCodes) {
    const token = code.text;
    if (!PATH_TOKEN.test(token)) continue;
    const rel = token.replace(/^\.\//, '');
    const first = rel.split('/')[0];
    if (!tops.has(first)) continue; // not a path into this repository (or a planned location)
    if (!exists(root, rel)) add('error', file, code.line, `\`${token}\` refers to a file that does not exist`);
  }
}

function checkPlaceholders(file, lines, patterns, add) {
  lines.forEach((line, i) => {
    for (const re of patterns) {
      if (re.test(line)) {
        add('error', file, i + 1, `placeholder or unfinished-work text matches /${re.source}/`);
        break;
      }
    }
  });
}

export function collectEnvVars(root, cfg) {
  const used = new Map();
  const patterns = [
    /process\.env\.([A-Z][A-Z0-9_]*)/g,
    /process\.env\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]/g,
    /import\.meta\.env\.([A-Z][A-Z0-9_]*)/g,
    /os\.environ(?:\.get)?\s*[[(]\s*['"]([A-Z][A-Z0-9_]*)['"]/g,
    /os\.getenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]/g,
    /environ\.get\(\s*['"]([A-Z][A-Z0-9_]*)['"]/g,
  ];
  for (const f of walk(root)) {
    if (!matchesAny(f, cfg.env.sourceGlobs) || matchesAny(f, cfg.env.excludeGlobs)) continue;
    const text = readText(root, f);
    for (const re of patterns) {
      for (const m of text.matchAll(re)) if (!used.has(m[1])) used.set(m[1], f);
    }
  }
  return used;
}

function checkEnv(root, cfg, add) {
  const ignored = new Set(cfg.env.ignoredNames ?? []);
  const exampleFile = cfg.env.exampleFile;
  const refDoc = cfg.env.referenceDoc;

  const exampleVars = new Set();
  if (exists(root, exampleFile)) {
    for (const line of readText(root, exampleFile).split(/\r?\n/)) {
      const m = /^([A-Z][A-Z0-9_]*)=/.exec(line);
      if (m) exampleVars.add(m[1]);
    }
  }
  const docVars = new Set();
  if (exists(root, refDoc)) {
    for (const m of readText(root, refDoc).matchAll(/`([A-Z][A-Z0-9_]+)`/g)) docVars.add(m[1]);
  }

  for (const [name, where] of collectEnvVars(root, cfg)) {
    if (ignored.has(name)) continue;
    if (!exampleVars.has(name)) add('error', where, 0, `environment variable ${name} is used but missing from ${exampleFile}`);
    if (!docVars.has(name)) add('error', where, 0, `environment variable ${name} is used but missing from ${refDoc}`);
  }
  for (const name of exampleVars) {
    if (!docVars.has(name)) add('error', exampleFile, 0, `environment variable ${name} is in ${exampleFile} but missing from ${refDoc}`);
  }
}

function checkModules(root, cfg, add) {
  for (const pattern of cfg.modules.roots) {
    if (!pattern.endsWith('/*')) continue;
    const parent = pattern.slice(0, -2);
    for (const name of listDirs(root, parent)) {
      if (name.startsWith('.') || name.startsWith('_') || name === 'node_modules') continue;
      const dir = `${parent}/${name}`;
      if (!exists(root, `${dir}/README.md`) && !exists(root, `docs/modules/${name}.md`)) {
        add('error', dir, 0, `module has no documentation (add ${dir}/README.md or docs/modules/${name}.md)`);
      }
    }
  }
}

/** Run every documentation check; returns [{ severity, file, line, message }]. */
export function checkDocs(root = ROOT) {
  const cfg = loadConfig(root);
  const findings = [];
  const add = (severity, file, line, message) => findings.push({ severity, file, line, message });

  for (const required of cfg.requiredFiles) {
    if (!exists(root, required)) add('error', required, 0, 'required file is missing');
  }

  const tops = topLevelNames(root);
  const patterns = (cfg.markdown.placeholderPatterns ?? []).map((p) => new RegExp(p.pattern, p.flags ?? ''));
  const anchorCache = new Map();

  for (const file of collectMarkdown(root, cfg)) {
    const a = analyze(readText(root, file));
    for (const link of a.links) checkLink(root, file, link, anchorCache, add);
    checkInlinePaths(root, file, a.inlineCodes, tops, add);
    checkPlaceholders(file, a.lines, patterns, add);
  }

  checkEnv(root, cfg, add);
  checkModules(root, cfg, add);
  return findings;
}

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  const findings = checkDocs(root);
  const errors = findings.filter((f) => f.severity === 'error');
  const checked = collectMarkdown(root, loadConfig(root)).length;

  if (errors.length === 0) {
    console.log(`check-docs: ${checked} Markdown files and all documentation rules passed.`);
    process.exit(EXIT.PASS);
  }
  console.error(`check-docs: ${errors.length} problem(s) in ${checked} Markdown files:`);
  for (const f of errors) console.error(`  ${f.file}${f.line ? `:${f.line}` : ''}  ${f.message}`);
  process.exit(EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
