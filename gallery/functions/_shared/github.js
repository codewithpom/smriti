// GitHub API calls made by the site: start the scan workflow, read its latest run, read/write repo variables.
// Every helper returns null/false instead of throwing, so a GitHub outage never breaks an upload.
import { storage, getJson, putJson } from './store.js';

const WORKFLOW = 'scan.yml';
const SCAN_BRANCH = 'automation/scan';
const QUEUED_FOR_MS = 10 * 60 * 1000; // a queued run drains every pending upload, so a burst needs only one dispatch

export function config(env) {
  const token = env.GITHUB_DISPATCH_TOKEN, repo = env.GITHUB_REPO;
  const ok = typeof token === 'string' && token.trim() && typeof repo === 'string' && /^[\w.-]+\/[\w.-]+$/.test(repo) && !/[\r\n]/.test(repo);
  return ok ? { token, repo } : null;
}

async function github(env, path, { method = 'GET', body } = {}) {
  const c = config(env);
  if (!c) return null;
  try {
    return await fetch(`https://api.github.com/repos/${c.repo}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${c.token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'smriti-admin',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    return null;
  }
}

export async function dispatch(env, inputs = {}) {
  const strings = Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, String(v)]));
  const response = await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, { method: 'POST', body: { ref: SCAN_BRANCH, inputs: strings } });
  return response?.status === 204;
}

export async function latestRun(env) {
  const response = await github(env, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  if (response?.status !== 200) return null;
  try {
    const run = (await response.json()).workflow_runs?.[0];
    return run ? { status: run.status, conclusion: run.conclusion, createdAt: run.created_at, url: run.html_url } : { none: true };
  } catch {
    return null;
  }
}

export async function getVariable(env, name) {
  const response = await github(env, `/actions/variables/${encodeURIComponent(name)}`);
  if (!response?.ok) return null; // 404 = not set yet
  try { return (await response.json()).value ?? null; } catch { return null; }
}

export async function setVariable(env, name, value) {
  const body = { name, value: String(value) };
  let response = await github(env, `/actions/variables/${encodeURIComponent(name)}`, { method: 'PATCH', body });
  if (response?.status === 404) response = await github(env, '/actions/variables', { method: 'POST', body });
  return !!response?.ok;
}

// GitHub's repository variable is the immediate hold control for scan.yml.
// Pages can also supply SCAN_HOLD locally for development and staged deploys.
export async function scanHold(env) {
  if (env.SCAN_HOLD === true || String(env.SCAN_HOLD ?? '').toLowerCase() === 'true') return true;
  return (await getVariable(env, 'SCAN_HOLD'))?.toLowerCase() === 'true';
}

// Record "queued" so the upload page can show it before the runner starts.
export async function markQueued(env, { heldForMigration = false } = {}) {
  const store = storage(env);
  const previous = await getJson(store, 'status/scan.json', {});
  const next = { ...previous, state: 'queued', queuedAt: new Date().toISOString() };
  if (heldForMigration) next.holdReason = 'storage_migration';
  else delete next.holdReason;
  await putJson(store, 'status/scan.json', next);
}

// Debounced dispatch used after uploads and reviews: 'queued' | 'already_queued' | 'dispatch_failed' | 'not_configured'.
export async function requestScan(env, inputs = {}) {
  if (await scanHold(env)) {
    await markQueued(env, { heldForMigration: true });
    return 'held';
  }
  if (!config(env)) return 'not_configured';
  const previous = await getJson(storage(env), 'status/scan.json', null);
  const queuedAt = Date.parse(previous?.queuedAt);
  if (previous?.state === 'queued' && Date.now() - queuedAt < QUEUED_FOR_MS) return 'already_queued';
  if (!await dispatch(env, inputs)) return 'dispatch_failed';
  await markQueued(env);
  return 'queued';
}
