import { requireApproved, errorResponse, jsonResponse } from '../_shared/auth.js';
import { storage } from '../_shared/store.js';
import { requestScan } from '../_shared/github.js';

const LIMIT = 10 * 1024 * 1024;
const MULTIPART_OVERHEAD_LIMIT = 128 * 1024;
const MAX_PENDING_PER_USER = 50; // free R2 allowance: one student cannot fill it
const SHA256 = /^[a-f0-9]{64}$/;
const TYPES = [
  { ext: 'jpg', mime: 'image/jpeg', test: b => b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  { ext: 'png', mime: 'image/png', test: b => b.length >= 8 && [137,80,78,71,13,10,26,10].every((v,i) => b[i] === v) },
  { ext: 'webp', mime: 'image/webp', test: b => b.length >= 12 && String.fromCharCode(...b.slice(0,4)) === 'RIFF' && String.fromCharCode(...b.slice(8,12)) === 'WEBP' },
];

async function readLimited(stream, limit = LIMIT) {
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new RangeError('too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const auth = await requireApproved(request, env);
  if (auth.response) return auth.response;
  if (env.UPLOAD_HOLD === true || String(env.UPLOAD_HOLD ?? '').toLowerCase() === 'true') {
    return errorResponse(503, 'upload_paused_for_storage_cutover');
  }
  let store, pending;
  try {
    store = storage(env);
    pending = await store.list({ prefix: `pending/${auth.user.uid}/` });
  } catch {
    return errorResponse(503, 'storage_unavailable');
  }
  if ((pending.objects ?? []).length >= MAX_PENDING_PER_USER) return errorResponse(429, 'too_many_pending');
  let requestBytes;
  try { requestBytes = await readLimited(request.body, LIMIT + MULTIPART_OVERHEAD_LIMIT); } catch (error) {
    return error instanceof RangeError ? errorResponse(413, 'too_large') : errorResponse(400, 'invalid_multipart');
  }
  let form;
  try {
    form = await new Request(request.url, { method: request.method, headers: request.headers, body: requestBytes }).formData();
  } catch { return errorResponse(400, 'invalid_multipart'); }
  const file = form.get('file');
  if (!file || typeof file.stream !== 'function') return errorResponse(400, 'file_required');
  let bytes;
  try { bytes = await readLimited(file.stream()); } catch (error) {
    return error instanceof RangeError ? errorResponse(413, 'too_large') : errorResponse(400, 'invalid_file');
  }
  const type = TYPES.find(candidate => candidate.test(bytes.subarray(0, 12)));
  if (!type) return errorResponse(400, 'unsupported_image');
  let sha, catalog, object;
  try {
    sha = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
  } catch { return errorResponse(400, 'invalid_file'); }
  try { object = await store.get('state/catalog.json'); }
  catch { return errorResponse(503, 'storage_unavailable'); }
  if (!object) return errorResponse(503, 'duplicate_check_unavailable');
  try {
    catalog = await new Response(object.body).json();
    if (!Array.isArray(catalog)) return errorResponse(503, 'duplicate_check_unavailable');
    if (catalog.some(entry => entry?.sha === sha || entry?.upload_sha === sha)) return errorResponse(409, 'duplicate');
  } catch { return errorResponse(503, 'duplicate_check_unavailable'); }
  if (!SHA256.test(sha)) return errorResponse(400, 'invalid_file');
  let existing;
  try { existing = await store.list({ prefix: `pending/${auth.user.uid}/${sha}/` }); }
  catch { return errorResponse(503, 'storage_unavailable'); }
  if ((existing.objects ?? []).length) return errorResponse(409, 'duplicate');
  const key = `pending/${auth.user.uid}/${sha}/${Date.now()}-${crypto.randomUUID()}.${type.ext}`;
  try { await store.put(key, bytes, { httpMetadata: { contentType: type.mime } }); }
  catch { return errorResponse(503, 'storage_unavailable'); }
  const scan = requestScan(env).catch(() => 'dispatch_failed');
  if (typeof context.waitUntil === 'function') context.waitUntil(scan); else await scan;
  return jsonResponse({ ok: true, key });
}
