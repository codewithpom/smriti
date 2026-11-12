// KV / R2 switch. The setting is the GitHub variable STORAGE_BACKEND (read by the scan workflow);
// the binding is what this deployment's PHOTOS actually is. They differ until the admin rebinds PHOTOS.
import { requireAdmin, errorResponse, jsonResponse } from '../../_shared/auth.js';
import { storage, getJson } from '../../_shared/store.js';
import { firebaseStorage, firebaseStorageConfigured } from '../../_shared/gcs.js';
import { config, getVariable, setVariable, scanHold } from '../../_shared/github.js';

const BACKENDS = ['kv', 'r2', 'firebase'];
const MIGRATION_KEY = 'migration/kv-to-firebase/manifest.json';
export const storageBinding = (env) => {
  if (env.PHOTOS_BACKEND === 'firebase') return 'firebase';
  return typeof env.PHOTOS?.getWithMetadata === 'function' ? 'kv' : env.PHOTOS ? 'r2' : null;
};

function migrationReady(value, env) {
  const counts = value?.last_counts;
  return value?.version === 1 && value?.phase === 'delta' && value?.status === 'stable' && value?.stable === true
    && /^[a-f0-9]{64}$/.test(value?.source_id ?? '') && value?.destination_id === env.FIREBASE_STORAGE_BUCKET
    && /^[a-f0-9]{64}$/.test(value?.inventory_sha256 ?? '')
    && counts?.read_misses === 0 && counts?.size_changes === 0 && counts?.listed_changed === false;
}

async function firebaseMigration(env) {
  if (!firebaseStorageConfigured(env)) return null;
  return getJson(firebaseStorage(env), MIGRATION_KEY, null);
}

export async function onRequestGet({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  const setting = await getVariable(env, 'STORAGE_BACKEND');
  const binding = storageBinding(env);
  const uploadHeld = env.UPLOAD_HOLD === true || String(env.UPLOAD_HOLD ?? '').toLowerCase() === 'true';
  let store;
  try { store = storage(env); } catch { store = null; }
  const [usage, migration, held] = await Promise.all([
    store ? getJson(store, 'status/storage.json', null) : null,
    firebaseMigration(env).catch(() => null),
    scanHold(env),
  ]);
  const ready = firebaseStorageConfigured(env) && (binding === 'firebase' || (migrationReady(migration, env) && held));
  return jsonResponse({
    ok: true,
    setting: BACKENDS.includes(setting) ? setting : null,
    binding,
    usage,
    configured: !!config(env),
    firebaseConfigured: firebaseStorageConfigured(env),
    migrationReady: migrationReady(migration, env),
    scanHeld: held,
    uploadHeld,
    firebaseReady: ready,
  });
}

export async function onRequestPost({ request, env }) {
  const auth = await requireAdmin(request, env);
  if (auth.response) return auth.response;
  let body;
  try { body = await request.json(); } catch { return errorResponse(400, 'invalid_json'); }
  if (!BACKENDS.includes(body?.backend)) return errorResponse(400, 'invalid_request');
  if (!config(env)) return errorResponse(501, 'not_configured');
  if (body.backend === 'firebase' && !firebaseStorageConfigured(env)) return errorResponse(501, 'storage_not_configured');
  if (body.backend === 'firebase' && storageBinding(env) !== 'firebase') {
    let migration;
    try { migration = await firebaseMigration(env); }
    catch { return errorResponse(503, 'storage_unavailable'); }
    if (!migrationReady(migration, env)) return errorResponse(409, 'migration_not_ready');
    if (!await scanHold(env)) return errorResponse(409, 'scan_hold_required');
  }
  if (!await setVariable(env, 'STORAGE_BACKEND', body.backend)) return errorResponse(502, 'storage_error');
  const binding = storageBinding(env);
  return jsonResponse({ ok: true, setting: body.backend, binding, mismatch: body.backend !== binding });
}
