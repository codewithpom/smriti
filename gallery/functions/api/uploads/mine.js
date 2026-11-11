// The caller's own uploads and the pipeline status. Uploads not yet seen by the pipeline show as "queued".
import { requireApproved, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage, getJson, listAll } from '../../_shared/store.js';

const MAX_ITEMS = 100;

function uploadedAt(key) {
  const ms = Number(key.split('/').at(-1).match(/^(\d+)-/)?.[1]);
  return Number.isFinite(ms) ? new Date(ms).toISOString() : null;
}

export async function onRequestGet({ request, env }) {
  const auth = await requireApproved(request, env);
  if (auth.response) return auth.response;
  const uid = auth.user.uid;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(uid)) return errorResponse(400, 'invalid_request');
  const store = storage(env);
  const status = await getJson(store, `status/uploads/${uid}.json`, {});
  const items = Array.isArray(status.items) ? [...status.items] : [];
  const known = new Set(items.map((item) => item.key));
  for (const object of await listAll(store, `pending/${uid}/`)) {
    if (!known.has(object.key)) items.push({ key: object.key, state: 'queued', at: uploadedAt(object.key) });
  }
  items.sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0));
  const scan = await getJson(store, 'status/scan.json', null);
  return jsonResponse({ ok: true, scan, items: items.slice(0, MAX_ITEMS) }, 200, { 'cache-control': 'private, no-store' });
}
