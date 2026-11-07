import { requireApproved, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage } from '../../_shared/store.js';

const ALLOWED = new Set(['photos', 'people', 'graph', 'questions', 'clusters']);

export async function onRequestGet({ request, env, params }) {
  const name = params.name;
  if (!ALLOWED.has(name)) return errorResponse(404, 'not_found');
  const auth = await requireApproved(request, env);
  if (auth.response) return auth.response;
  const object = await storage(env).get(`data/${name}.json`);
  if (!object) return errorResponse(404, 'not_found');
  return new Response(object.body, { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' } });
}
