import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { config, dispatch, latestRun, markQueued, scanHold } from '../../_shared/github.js';

const FLAGS = ['fetchFacebook', 'recluster', 'reembed'];

async function guard(request, env) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  if (!config(env)) return errorResponse(501, 'not_configured');
  return null;
}

export async function onRequestGet({ request, env }) {
  const denied = await guard(request, env);
  if (denied) return denied;
  const held = await scanHold(env);
  const run = await latestRun(env);
  return run ? jsonResponse({ ...run, scanHeld: held }) : errorResponse(502, 'dispatch_failed');
}

// Admin "Run scan now": skips the upload debounce, since the admin may want different inputs.
export async function onRequestPost({ request, env }) {
  const denied = await guard(request, env);
  if (denied) return denied;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  const valid = body && typeof body === 'object' && !Array.isArray(body)
    && FLAGS.every((k) => body[k] === undefined || typeof body[k] === 'boolean')
    && (body.migrateFrom === undefined || ['kv', 'r2', 'firebase'].includes(body.migrateFrom));
  if (!valid) return errorResponse(400, 'invalid_request');
  if (await scanHold(env)) {
    await markQueued(env, { heldForMigration: true });
    return errorResponse(409, 'scan_paused_for_storage_migration');
  }
  const ok = await dispatch(env, {
    fetch_facebook: !!body.fetchFacebook, recluster: !!body.recluster, reembed: !!body.reembed, migrate_from: body.migrateFrom ?? '',
  });
  if (!ok) return errorResponse(502, 'dispatch_failed');
  await markQueued(env);
  return jsonResponse({ ok: true });
}
