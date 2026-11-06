import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const objects = [];
  let cursor;
  do {
    const listed = await env.PHOTOS.list({ prefix: 'pending/', cursor });
    objects.push(...listed.objects);
    cursor = listed.truncated ? listed.cursor : undefined;
  } while (cursor);
  return jsonResponse({ ok: true, uploads: objects.map(object => {
    const [, uid, filename = ''] = object.key.split('/');
    const match = filename.match(/^(\d+)-/);
    return { key: object.key, size: object.size, uid, time: match ? new Date(Number(match[1])).toISOString() : null };
  }) });
}

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  const { key, action } = body ?? {};
  if (typeof key !== 'string' || !/^pending\/[A-Za-z0-9_-]+\/[0-9]+-[0-9a-f-]+\.(jpg|png|webp)$/.test(key) || !['approve', 'reject'].includes(action)) return errorResponse(400, 'invalid_request');
  const object = await env.PHOTOS.get(key);
  if (!object) return errorResponse(404, 'not_found');
  if (action === 'approve') await env.PHOTOS.put(`inbox/${crypto.randomUUID()}.${key.split('.').pop()}`, object.body, { httpMetadata: object.httpMetadata });
  await env.PHOTOS.delete(key);
  return jsonResponse({ ok: true });
}
