// Security lint for GitHub Actions workflows:
//   - every third-party action is pinned to a full 40-character commit SHA
//   - workflow-level (or every job-level) "permissions" is declared
//   - no pull_request_target trigger
//   - no untrusted event data interpolated into run: scripts (script injection)
//   - every job sets timeout-minutes
//
// Usage: node scripts/validation/check-workflows.mjs [--root <dir>]
import { ROOT, EXIT, walk, readText, isMain, argValue } from '../lib/fsutil.mjs';

const SHA_PIN = /^[^@\s]+@[0-9a-f]{40}$/;
const DANGEROUS = /\$\{\{\s*(?:github\.event\.(?:issue|pull_request|comment|review|head_commit|commits|pages|discussion)|github\.head_ref)/;

async function loadYaml() {
  try {
    const mod = await import('yaml');
    return mod.default ?? mod;
  } catch {
    return null;
  }
}

export function lintWorkflow(file, doc) {
  const errors = [];
  const wf = doc ?? {};
  const triggers = wf.on ?? wf.true ?? {};
  const triggerNames = typeof triggers === 'string' ? [triggers] : Array.isArray(triggers) ? triggers : Object.keys(triggers);
  if (triggerNames.includes('pull_request_target')) {
    errors.push(`${file}: uses pull_request_target, which runs with secrets against untrusted code`);
  }

  const jobs = wf.jobs ?? {};
  for (const [jobId, job] of Object.entries(jobs)) {
    if (wf.permissions === undefined && job.permissions === undefined) {
      errors.push(`${file}: job "${jobId}" has no permissions (declare least privilege at workflow or job level)`);
    }
    if (job['timeout-minutes'] === undefined && job.uses === undefined) {
      errors.push(`${file}: job "${jobId}" has no timeout-minutes`);
    }
    const uses = [];
    if (typeof job.uses === 'string') uses.push(job.uses);
    for (const step of job.steps ?? []) {
      if (typeof step.uses === 'string') uses.push(step.uses);
      if (typeof step.run === 'string' && DANGEROUS.test(step.run)) {
        errors.push(`${file}: job "${jobId}" interpolates untrusted event data into a run script (pass it through env instead)`);
      }
    }
    for (const u of uses) {
      if (u.startsWith('./')) continue;
      if (u.startsWith('docker://')) {
        if (!/@sha256:[0-9a-f]{64}$/.test(u)) errors.push(`${file}: "${u}" is not pinned to an image digest`);
        continue;
      }
      if (!SHA_PIN.test(u)) errors.push(`${file}: "${u}" is not pinned to a full commit SHA`);
    }
  }
  if (wf.permissions === undefined && Object.keys(jobs).length === 0) {
    errors.push(`${file}: no jobs defined`);
  }
  return errors;
}

export async function checkWorkflows(root = ROOT) {
  const YAML = await loadYaml();
  const files = walk(root, '.github/workflows').filter((f) => /\.ya?ml$/.test(f));
  if (files.length === 0) return { errors: [], checked: 0, skipped: false };
  if (!YAML) return { errors: [], checked: 0, skipped: true };

  const errors = [];
  for (const file of files) {
    let doc;
    try {
      doc = YAML.parse(readText(root, file));
    } catch (e) {
      errors.push(`${file}: cannot parse (${e.message.split('\n')[0]})`);
      continue;
    }
    errors.push(...lintWorkflow(file, doc));
  }
  return { errors, checked: files.length, skipped: false };
}

async function main() {
  const root = argValue(process.argv.slice(2), '--root') ?? ROOT;
  const r = await checkWorkflows(root);
  if (r.skipped) {
    console.log('check-workflows: SKIPPED (the "yaml" package is not installed; run "npm ci").');
    process.exit(EXIT.SKIPPED);
  }
  if (r.errors.length > 0) {
    console.error(`check-workflows: ${r.errors.length} problem(s):`);
    for (const e of r.errors) console.error(`  ${e}`);
    process.exit(EXIT.FAIL);
  }
  console.log(`check-workflows: ${r.checked} workflow file(s) pass the security lint.`);
  process.exit(EXIT.PASS);
}

if (isMain(import.meta.url)) await main();
