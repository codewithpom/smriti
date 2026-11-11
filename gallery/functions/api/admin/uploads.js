// Review of uploads the pipeline has already processed (duplicates removed, faces found).
// A decision is a review/<photo>.json marker; the next pipeline run publishes or removes the photo.
import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage, getJson, putJson, listAll } from '../../_shared/store.js';
import { requestScan } from '../../_shared/github.js';

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const store = storage(env);
  const pending = await getJson(store, 'data/pending.json', {});
  const queued = (await listAll(store, 'pending/')).length; // the pipeline deletes raw uploads once processed
  return jsonResponse({ ok: true, items: Array.isArray(pending.items) ? pending.items : [], queued });
}

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  if (!/^p[0-9a-f]{8}$/.test(body?.photo ?? '') || !['approve', 'reject'].includes(body.action)) return errorResponse(400, 'invalid_request');
  await putJson(storage(env), `review/${body.photo}.json`, { action: body.action, by: auth.user.uid, at: new Date().toISOString() });
  await requestScan(env);
  return jsonResponse({ ok: true });
}
