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
  const queuedItems = (await listAll(store, 'pending/')).map(({ key }) => {
    const parts = key.split('/');
    const leaf = parts.at(-1);
    const ms = Number(leaf.match(/^(\d+)-/)?.[1]);
    return { key, uid: parts[1], at: Number.isFinite(ms) ? new Date(ms).toISOString() : null };
  });
  return jsonResponse({ ok: true, items: Array.isArray(pending.items) ? pending.items : [], queuedItems, queued: queuedItems.length });
}

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  if (body?.action === 'approve-user') {
    if (typeof body.uid !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(body.uid)) return errorResponse(400, 'invalid_request');
    const store = storage(env);
    const pending = await getJson(store, 'data/pending.json', null);
    if (!Array.isArray(pending?.items)) return errorResponse(503, 'uploads_unavailable');
    const photos = [...new Set(pending.items
      .filter(item => item?.uploader === body.uid && /^p[0-9a-f]{8}$/.test(item?.photo ?? ''))
      .map(item => item.photo))];
    let uploadKeys;
    try {
      uploadKeys = (await listAll(store, `pending/${body.uid}/`)).map(item => item.key)
        .filter(key => key.startsWith(`pending/${body.uid}/`));
    } catch { return errorResponse(503, 'storage_unavailable'); }
    if (!photos.length && !uploadKeys.length) return errorResponse(409, 'no_uploads_to_approve');
    try {
      const key = `review/batches/${crypto.randomUUID()}.json`;
      await putJson(store, key, { action: 'approve', photos, uploadKeys, by: auth.user.uid, at: new Date().toISOString() });
    } catch { return errorResponse(503, 'storage_unavailable'); }
    await requestScan(env);
    return jsonResponse({ ok: true, approved: photos.length + uploadKeys.length, queued: uploadKeys.length });
  }
  if (!/^p[0-9a-f]{8}$/.test(body?.photo ?? '') || !['approve', 'reject'].includes(body.action)) return errorResponse(400, 'invalid_request');
  await putJson(storage(env), `review/${body.photo}.json`, { action: body.action, by: auth.user.uid, at: new Date().toISOString() });
  await requestScan(env);
  return jsonResponse({ ok: true });
}
