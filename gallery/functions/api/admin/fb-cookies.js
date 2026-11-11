import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage } from '../../_shared/store.js';
import { toNetscape } from '../../_shared/cookies.js';

const COOKIE_KEY = 'secrets/fb-cookies.txt';
const URL_KEY = 'secrets/fb-url.txt';

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  let converted;
  try {
    if (typeof body?.cookies !== 'string') throw new Error('invalid_cookies');
    converted = toNetscape(body.cookies);
  } catch { return errorResponse(400, 'invalid_cookies'); }
  if (body.pageUrl !== undefined && (typeof body.pageUrl !== 'string' ||
      !/^https:\/\/(www\.)?facebook\.com\/[A-Za-z0-9._-]+(\/photos)?\/?$/.test(body.pageUrl) || /[\r\n]/.test(body.pageUrl))) return errorResponse(400, 'invalid_request');
  try {
    const store = storage(env);
    const options = { httpMetadata: { contentType: 'text/plain' } };
    await store.put(COOKIE_KEY, converted.text, options);
    if (body.pageUrl !== undefined) await store.put(URL_KEY, body.pageUrl, options);
    return jsonResponse({ ok: true, count: converted.count });
  } catch { return errorResponse(500, 'storage_error'); }
}

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  try {
    const store = storage(env);
    const cookie = await store.get(COOKIE_KEY);
    const url = await store.get(URL_KEY);
    const bytes = cookie ? cookie.size ?? (await new Response(cookie.body).arrayBuffer()).byteLength : 0;
    return jsonResponse({ present: !!cookie, bytes, updated: cookie?.uploaded ? new Date(cookie.uploaded).toISOString() : null, hasUrl: !!url });
  } catch { return errorResponse(500, 'storage_error'); }
}

export async function onRequestDelete({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  try {
    const store = storage(env);
    await store.delete(COOKIE_KEY);
    await store.delete(URL_KEY);
    return jsonResponse({ ok: true });
  } catch { return errorResponse(500, 'storage_error'); }
}
