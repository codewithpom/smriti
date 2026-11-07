import { requireApproved, errorResponse, jsonResponse } from '../_shared/auth.js';
import { storage } from '../_shared/store.js';

const LIMIT = 10 * 1024 * 1024;
const MULTIPART_OVERHEAD_LIMIT = 128 * 1024;
const MAX_PENDING_PER_USER = 50; // free R2 allowance: one student cannot fill it
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

export async function onRequestPost({ request, env }) {
  const auth = await requireApproved(request, env);
  if (auth.response) return auth.response;
  const pending = await storage(env).list({ prefix: `pending/${auth.user.uid}/` });
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
  const key = `pending/${auth.user.uid}/${Date.now()}-${crypto.randomUUID()}.${type.ext}`;
  await storage(env).put(key, bytes, { httpMetadata: { contentType: type.mime } });
  return jsonResponse({ ok: true, key });
}
