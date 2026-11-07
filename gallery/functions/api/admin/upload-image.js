import { requireAdmin, errorResponse } from '../../_shared/auth.js';
import { storage } from '../../_shared/store.js';

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const key = new URL(request.url).searchParams.get('key');
  if (!key || !/^pending\/[A-Za-z0-9_-]+\/[0-9]+-[0-9a-f-]+\.(jpg|png|webp)$/.test(key)) return errorResponse(400, 'invalid_key');
  const object = await storage(env).get(key);
  if (!object) return errorResponse(404, 'not_found');
  return new Response(object.body, { headers: { 'content-type': object.httpMetadata?.contentType ?? 'application/octet-stream', 'cache-control': 'private, no-store', 'x-content-type-options': 'nosniff' } });
}
