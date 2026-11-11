// KV / R2 switch. The setting is the GitHub variable STORAGE_BACKEND (read by the scan workflow);
// the binding is what this deployment's PHOTOS actually is. They differ until the admin rebinds PHOTOS.
import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage, getJson } from '../../_shared/store.js';
import { config, getVariable, setVariable } from '../../_shared/github.js';

const BACKENDS = ['kv', 'r2'];
const bindingOf = (env) => (typeof env.PHOTOS?.getWithMetadata === 'function' ? 'kv' : 'r2');

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const setting = await getVariable(env, 'STORAGE_BACKEND');
  return jsonResponse({
    ok: true,
    setting: BACKENDS.includes(setting) ? setting : null,
    binding: bindingOf(env),
    usage: await getJson(storage(env), 'status/storage.json', null),
    configured: !!config(env),
  });
}

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  if (!BACKENDS.includes(body?.backend)) return errorResponse(400, 'invalid_request');
  if (!config(env)) return errorResponse(501, 'not_configured');
  if (!await setVariable(env, 'STORAGE_BACKEND', body.backend)) return errorResponse(502, 'storage_error');
  const binding = bindingOf(env);
  return jsonResponse({ ok: true, setting: body.backend, binding, mismatch: body.backend !== binding });
}
