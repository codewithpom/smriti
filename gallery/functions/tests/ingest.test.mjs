// Auto-ingest endpoints: debounced dispatch, per-user upload status, storage switch, corrections hand-off.
import { afterEach, beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign as rsaSign } from 'node:crypto';
import { resetJwksCache } from '../_shared/auth.js';
import { getJson, putJson } from '../_shared/store.js';
import { requestScan } from '../_shared/github.js';
import { onRequestGet as mine } from '../api/uploads/mine.js';
import { onRequestGet as storageGet, onRequestPost as storagePost } from '../api/admin/storage.js';
import { onRequestPost as sendCorrections } from '../api/admin/corrections.js';

const PROJECT = 'student-gallery-test';
const BASE = { FIREBASE_PROJECT_ID: PROJECT, ADMIN_EMAILS: 'admin@example.com' };
const GH = { GITHUB_DISPATCH_TOKEN: 'test-token', GITHUB_REPO: 'owner/repo' };
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'test-key', alg: 'RS256', use: 'sig' };
let objects, github, statusByUid, firebaseManifest, realFetch;

const b64 = (value) => Buffer.from(value).toString('base64url');
function jwt(uid) {
  const email = uid === 'admin' ? 'admin@example.com' : 'rkmv1234@gmail.com';
  const head = b64(JSON.stringify({ alg: 'RS256', kid: 'test-key', typ: 'JWT' }));
  const body = b64(JSON.stringify({ aud: PROJECT, iss: `https://securetoken.google.com/${PROJECT}`, sub: uid, email, email_verified: true, exp: Math.floor(Date.now() / 1000) + 3600 }));
  return `${head}.${body}.${rsaSign('RSA-SHA256', Buffer.from(`${head}.${body}`), privateKey).toString('base64url')}`;
}
const call = (path, { as = 'student-1', method = 'GET', body } = {}) =>
  new Request(`https://gallery.test${path}`, { method, headers: { authorization: `Bearer ${jwt(as)}`, 'content-type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });

// R2-shaped bucket whose get() returns a stream body, like the real binding.
function r2() {
  return {
    async get(key) { const v = objects.get(key); return v ? { body: new Blob([v]).stream(), size: v.byteLength } : null; },
    async put(key, body) { objects.set(key, new Uint8Array(await new Response(body).arrayBuffer())); },
    async delete(key) { objects.delete(key); },
    async list({ prefix }) { return { objects: [...objects.keys()].filter((k) => k.startsWith(prefix)).map((key) => ({ key, size: objects.get(key).byteLength })) }; },
  };
}
function kv() {
  return {
    async getWithMetadata(key) { const v = objects.get(key); return { value: v ? v.buffer.slice(0) : null, metadata: null }; },
    async put(key, body) { objects.set(key, new Uint8Array(body)); },
    async list({ prefix }) { return { keys: [...objects.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true }; },
    async delete(key) { objects.delete(key); },
  };
}

function fakeFetch() {
  return async (url, init = {}) => {
    if (url.startsWith('https://www.googleapis.com/')) return Response.json({ keys: [jwk] }, { headers: { 'cache-control': 'max-age=3600' } });
    if (url === 'https://oauth2.googleapis.com/token') return Response.json({ access_token: 'firebase-test-token', expires_in: 3600 });
    if (url.startsWith('https://storage.googleapis.com/storage/v1/')) return firebaseManifest ? Response.json(firebaseManifest) : new Response('{}', { status: 404 });
    if (url.startsWith('https://firestore.googleapis.com/')) {
      const uid = decodeURIComponent(url.split('/').pop());
      const status = statusByUid.get(uid);
      return status ? Response.json({ fields: { email: { stringValue: 'rkmv1234@gmail.com' }, regno: { stringValue: '1234' }, status: { stringValue: status } } }) : new Response('{}', { status: 404 });
    }
    if (url.startsWith('https://api.github.com/')) { github.calls.push({ url, method: init.method || 'GET', body: init.body }); return github.reply(url, init); }
    throw new Error(`unexpected fetch ${url}`);
  };
}
// Hooks are scoped to this suite: with --test-isolation=none a top-level hook would run for every file.
describe('auto-ingest endpoints', () => {
  // Tests run in one process (--test-isolation=none): install the fake per test and put the previous one back.
  beforeEach(() => {
    realFetch = globalThis.fetch;
    globalThis.fetch = fakeFetch();
    resetJwksCache();
    objects = new Map();
    firebaseManifest = null;
    statusByUid = new Map([['student-1', 'approved'], ['student-2', 'approved'], ['pending', 'pending'], ['admin', 'approved']]);
    github = { calls: [], reply: () => new Response(null, { status: 204 }) };
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    resetJwksCache();
  });

  test('getJson reads stream (R2) and ArrayBuffer (KV) bodies', async () => {
    for (const make of [r2, kv]) {
      objects = new Map();
      const { storage } = await import('../_shared/store.js');
      const store = storage({ PHOTOS: make() });
      await putJson(store, 'status/x.json', { a: 1 });
      assert.deepEqual(await getJson(store, 'status/x.json'), { a: 1 });
      assert.equal(await getJson(store, 'missing.json', 'fallback'), 'fallback');
    }
  });

  test('requestScan debounces a fresh queued run and dispatches otherwise', async () => {
    const env = { ...GH, PHOTOS: r2() };
    assert.equal(await requestScan({ PHOTOS: r2() }), 'not_configured');
    objects.set('status/scan.json', new TextEncoder().encode(JSON.stringify({ state: 'queued', queuedAt: new Date().toISOString() })));
    assert.equal(await requestScan(env), 'already_queued');
    assert.equal(github.calls.filter((call) => call.url.endsWith('/actions/workflows/scan.yml/dispatches')).length, 0);
    objects.set('status/scan.json', new TextEncoder().encode(JSON.stringify({ state: 'running', stage: 'faces' })));
    assert.equal(await requestScan(env), 'queued');
    assert.equal(github.calls.filter((call) => call.url.endsWith('/actions/workflows/scan.yml/dispatches')).length, 1);
    const status = await getJson((await import('../_shared/store.js')).storage(env), 'status/scan.json');
    assert.equal(status.state, 'queued');
    assert.equal(status.stage, 'faces');
    github.reply = () => new Response('nope', { status: 500 });
    objects.set('status/scan.json', new TextEncoder().encode(JSON.stringify({ state: 'done' })));
    assert.equal(await requestScan(env), 'dispatch_failed');
    assert.equal((await getJson((await import('../_shared/store.js')).storage(env), 'status/scan.json')).state, 'done');
  });

  test('storage scan hold keeps uploads queued without dispatching a workflow', async () => {
    const env = { ...GH, PHOTOS: r2(), SCAN_HOLD: true };
    objects.set('pending/student-1/123-a.jpg', new Uint8Array([1]));
    assert.equal(await requestScan(env), 'held');
    assert.ok(objects.has('pending/student-1/123-a.jpg'));
    assert.equal(github.calls.length, 0);
    const held = await getJson((await import('../_shared/store.js')).storage(env), 'status/scan.json');
    assert.equal(held.state, 'queued');
    assert.equal(held.holdReason, 'storage_migration');
    assert.ok(Date.parse(held.queuedAt));
  });

  test('mine merges processed items with queued raw uploads and hides other users', async () => {
    const env = { ...BASE, PHOTOS: r2() };
    const enc = (v) => new TextEncoder().encode(JSON.stringify(v));
    objects.set('status/uploads/student-1.json', enc({ items: [{ key: 'pending/student-1/1000-a.jpg', state: 'duplicate', dupOf: 'p00000001', at: '2026-01-01T00:00:00.000Z' }] }));
    objects.set('pending/student-1/2000000000000-b.jpg', new Uint8Array([1]));
    objects.set('pending/student-2/2000000000001-c.jpg', new Uint8Array([1]));
    objects.set('status/scan.json', enc({ state: 'running', stage: 'faces', step: 4, steps: 8 }));
    const response = await mine({ request: call('/api/uploads/mine'), env });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.deepEqual(body.items.map((i) => i.state), ['queued', 'duplicate']);
    assert.ok(body.items.every((i) => i.key.startsWith('pending/student-1/')));
    assert.equal(body.scan.stage, 'faces');
    assert.equal((await mine({ request: call('/api/uploads/mine', { as: 'pending' }), env })).status, 403);
  });

  test('storage switch validates, reports the binding and falls back to creating the variable', async () => {
    github.reply = (url, init) => {
      if (init.method === 'PATCH') return new Response('{}', { status: 404 });
      if (init.method === 'POST') return new Response('{}', { status: 201 });
      return Response.json({ value: 'r2' });
    };
    const env = { ...BASE, ...GH, PHOTOS: kv() };
    const got = await (await storageGet({ request: call('/api/admin/storage', { as: 'admin' }), env })).json();
    assert.deepEqual([got.setting, got.binding, got.configured], ['r2', 'kv', true]);
    assert.equal((await storagePost({ request: call('/api/admin/storage', { as: 'admin', method: 'POST', body: { backend: 's3' } }), env })).status, 400);
    const set = await (await storagePost({ request: call('/api/admin/storage', { as: 'admin', method: 'POST', body: { backend: 'r2' } }), env })).json();
    assert.equal(set.mismatch, true);
    assert.deepEqual(github.calls.slice(-2).map((c) => c.method), ['PATCH', 'POST']);
    assert.equal((await storagePost({ request: call('/api/admin/storage', { as: 'admin', method: 'POST', body: { backend: 'kv' } }), env: { ...BASE, PHOTOS: kv() } })).status, 501);
    assert.equal((await storageGet({ request: call('/api/admin/storage'), env })).status, 403);
  });

  test('Firebase Storage can be selected for migration while the live KV route stays active', async () => {
    github.reply = () => new Response(null, { status: 204 });
    const baseEnv = {
      ...BASE, ...GH, PHOTOS: kv(),
      FIREBASE_STORAGE_BUCKET: 'smriti-private-test.appspot.com',
      GOOGLE_SERVICE_ACCOUNT_JSON: JSON.stringify({
        client_email: 'worker-test@example.iam.gserviceaccount.com',
        private_key: privateKey.export({ type: 'pkcs8', format: 'pem' }),
      }),
    };
    const env = { ...baseEnv, SCAN_HOLD: 'true' };
    const current = await (await storageGet({ request: call('/api/admin/storage', { as: 'admin' }), env })).json();
    assert.deepEqual([current.binding, current.firebaseConfigured, current.firebaseReady], ['kv', true, false]);
    const post = (targetEnv) => storagePost({
      request: call('/api/admin/storage', { as: 'admin', method: 'POST', body: { backend: 'firebase' } }), env: targetEnv,
    });
    assert.equal((await post(env)).status, 409, 'credentials alone do not establish data readiness');
    const stale = { version: 1, phase: 'delta', status: 'syncing', stable: false };
    firebaseManifest = stale;
    assert.equal((await post(env)).status, 409, 'unverified migration state is rejected');
    const verified = { version: 1, phase: 'delta', status: 'stable', stable: true,
      source_id: 'a'.repeat(64), destination_id: 'smriti-private-test.appspot.com', inventory_sha256: 'b'.repeat(64),
      last_counts: { read_misses: 0, size_changes: 0, listed_changed: false } };
    firebaseManifest = verified;
    assert.equal((await post({ ...baseEnv, SCAN_HOLD: 'false' })).status, 409, 'verified readiness still requires scans to be held');
    assert.equal((await post({ ...env, FIREBASE_STORAGE_BUCKET: 'other.appspot.com' })).status, 409, 'a manifest for another bucket cannot authorize selection');
    const set = await (await storagePost({
      request: call('/api/admin/storage', { as: 'admin', method: 'POST', body: { backend: 'firebase' } }), env,
    })).json();
    assert.deepEqual(set, { ok: true, setting: 'firebase', binding: 'kv', mismatch: true });
    assert.equal(env.PHOTOS_BACKEND, undefined, 'migration selection does not switch live uploads off KV');
    assert.equal(github.calls.at(-1).method, 'PATCH');

    const missingSecret = { ...env, GOOGLE_SERVICE_ACCOUNT_JSON: undefined };
    assert.equal((await post(missingSecret)).status, 501);

    const liveFirebase = { ...baseEnv, PHOTOS: undefined, PHOTOS_BACKEND: 'firebase', SCAN_HOLD: false };
    assert.equal((await post(liveFirebase)).status, 200, 'already-active Firebase remains valid after hold release');
  });

  test('corrections are validated, stored for the pipeline and trigger a scan', async () => {
    const env = { ...BASE, ...GH, PHOTOS: r2() };
    const ok = await sendCorrections({ request: call('/api/admin/corrections', { as: 'admin', method: 'POST', body: { faces: { 'p00000001:0': 'S1' }, scenes: {} } }), env });
    assert.equal(ok.status, 200);
    const stored = JSON.parse(new TextDecoder().decode(objects.get('review/corrections.json')));
    assert.deepEqual(stored, { faces: { 'p00000001:0': 'S1' }, scenes: {}, skipped: [], history: [] });
    assert.equal(github.calls.filter((call) => call.url.endsWith('/actions/workflows/scan.yml/dispatches')).length, 1);
    assert.equal((await sendCorrections({ request: call('/api/admin/corrections', { as: 'admin', method: 'POST', body: { faces: { a: 1 }, scenes: {} } }), env })).status, 400);
    assert.equal((await sendCorrections({ request: call('/api/admin/corrections', { method: 'POST', body: { faces: {}, scenes: {} } }), env })).status, 403);
  });
});
