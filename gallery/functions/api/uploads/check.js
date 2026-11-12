// Check exact hashes before a browser sends image bytes to the storage backend.
import { requireApproved, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { listAll, storage } from '../../_shared/store.js';

const MAX_HASHES = 30;
const SHA256 = /^[a-f0-9]{64}$/;

export async function onRequestPost({ request, env }) {
  const auth = await requireApproved(request, env);
  if (auth.response) return auth.response;

  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  if (!Array.isArray(body?.hashes) || body.hashes.length < 1 || body.hashes.length > MAX_HASHES ||
      body.hashes.some(hash => typeof hash !== 'string' || !SHA256.test(hash))) {
    return errorResponse(400, 'invalid_request');
  }

  try {
    const store = storage(env);
    const object = await store.get('state/catalog.json');
    if (!object) return errorResponse(503, 'duplicate_check_unavailable');
    const catalog = await new Response(object.body).json();
    if (!Array.isArray(catalog)) return errorResponse(503, 'duplicate_check_unavailable');
    const known = new Set(catalog.flatMap(entry => [entry?.sha, entry?.upload_sha])
      .filter(hash => typeof hash === 'string' && SHA256.test(hash)));
    const queued = await listAll(store, `pending/${auth.user.uid}/`);
    const queuedHashes = new Set(queued.map(({ key }) => key.split('/')[2]).filter(hash => SHA256.test(hash ?? '')));
    return jsonResponse({ ok: true, items: [...new Set(body.hashes)].map(sha => ({
      sha,
      state: known.has(sha) ? 'archive' : queuedHashes.has(sha) ? 'queued' : 'new',
    })) }, 200, { 'cache-control': 'private, no-store' });
  } catch {
    return errorResponse(503, 'duplicate_check_unavailable');
  }
}
