// Export tab "Send to the pipeline": the tallied corrections go to review/corrections.json,
// which the next scan merges into its state, so student answers retrain the face matcher.
import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage, putJson } from '../../_shared/store.js';
import { requestScan } from '../../_shared/github.js';

const MAX_BYTES = 2 * 1024 * 1024;
const isMap = (v) => v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).every((x) => typeof x === 'string');

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const text = await request.text();
  if (text.length > MAX_BYTES) return errorResponse(413, 'too_large');
  let body;
  try { body = JSON.parse(text); } catch { return errorResponse(400, 'invalid_json'); }
  const valid = isMap(body?.faces) && isMap(body?.scenes)
    && (body.skipped === undefined || Array.isArray(body.skipped)) && (body.history === undefined || Array.isArray(body.history));
  if (!valid) return errorResponse(400, 'invalid_request');
  await putJson(storage(env), 'review/corrections.json', { faces: body.faces, scenes: body.scenes, skipped: body.skipped ?? [], history: body.history ?? [] });
  await requestScan(env);
  return jsonResponse({ ok: true });
}
