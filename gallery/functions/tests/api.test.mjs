import test, { before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign as rsaSign } from 'node:crypto';
import { resetJwksCache } from '../_shared/auth.js';
import { onRequestGet as getData } from '../api/data/[name].js';
import { onRequestGet as getMe } from '../api/me.js';
import { onRequestPost as postUpload } from '../api/upload.js';
import { onRequestPost as checkUploads } from '../api/uploads/check.js';
import { onRequestGet as getUploads, onRequestPost as actOnUpload } from '../api/admin/uploads.js';
import { onRequestGet as getUploadImage } from '../api/admin/upload-image.js';

const PROJECT = 'student-gallery-test';
const ENV = { FIREBASE_PROJECT_ID: PROJECT, ADMIN_EMAILS: 'admin@example.com, second@example.com' };
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'test-key', alg: 'RS256', use: 'sig' };
let statusByUid;
let objects;

function base64url(value) { return Buffer.from(value).toString('base64url'); }
function token({ uid = 'student-1', email = 'rkmv1234@gmail.com', exp = Math.floor(Date.now() / 1000) + 3600, verified = true } = {}) {
  const head = base64url(JSON.stringify({ alg: 'RS256', kid: 'test-key', typ: 'JWT' }));
  const payload = base64url(JSON.stringify({ aud: PROJECT, iss: `https://securetoken.google.com/${PROJECT}`, sub: uid, email, email_verified: verified, exp }));
  const input = `${head}.${payload}`;
  return `${input}.${rsaSign('RSA-SHA256', Buffer.from(input), privateKey).toString('base64url')}`;
}
function request(path, { method = 'GET', as = 'student-1', jwt = token({ uid: as, email: as === 'admin' ? 'ADMIN@example.com' : 'rkmv1234@gmail.com' }), headers = {}, ...options } = {}) {
  return new Request(`https://gallery.test${path}`, { method, ...options, headers: { authorization: `Bearer ${jwt}`, ...headers } });
}
function r2() {
  return {
    async get(key) {
      const item = objects.get(key);
      return item && { body: item.body.slice(), httpMetadata: item.httpMetadata };
    },
    async put(key, body, options = {}) {
      const bytes = body instanceof Uint8Array ? body.slice() : new Uint8Array(await new Response(body).arrayBuffer());
      objects.set(key, { body: bytes, size: bytes.byteLength, httpMetadata: options.httpMetadata });
    },
    async delete(key) { objects.delete(key); },
    async list({ prefix }) {
      return { objects: [...objects].filter(([key]) => key.startsWith(prefix)).map(([key, value]) => ({ key, size: value.size })) };
    },
  };
}
function responseFor(url, init = {}) {
  if (url === 'https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com') {
    return new Response(JSON.stringify({ keys: [jwk] }), { headers: { 'cache-control': 'public, max-age=3600' } });
  }
  if (url.startsWith('https://firestore.googleapis.com/')) {
    assert.match(init.headers.authorization, /^Bearer /);
    const uid = decodeURIComponent(url.split('/').pop());
    const status = statusByUid.get(uid);
    return status ? Response.json({ fields: { email: { stringValue: uid === 'admin' ? 'admin@example.com' : 'rkmv1234@gmail.com' }, regno: { stringValue: '1234' }, status: { stringValue: status } } }) : new Response('{}', { status: 404 });
  }
  throw new Error(`Unexpected fetch: ${url}`);
}

before(() => {
  globalThis.fetch = responseFor;
});
beforeEach(() => {
  resetJwksCache();
  statusByUid = new Map([['student-1', 'approved'], ['pending', 'pending'], ['admin', 'approved']]);
  objects = new Map();
});

test('data route requires approval and allows only contract names', async () => {
  const bucket = r2();
  assert.equal((await getData({ request: request('/api/data/photos'), env: { ...ENV, PHOTOS: bucket }, params: { name: 'photos' } })).status, 404);
  await bucket.put('data/photos.json', new TextEncoder().encode('[1]'));
  const ok = await getData({ request: request('/api/data/photos'), env: { ...ENV, PHOTOS: bucket }, params: { name: 'photos' } });
  assert.equal(ok.status, 200);
  assert.equal(await ok.text(), '[1]');
  const bad = await getData({ request: request('/api/data/../../secrets'), env: { ...ENV, PHOTOS: bucket }, params: { name: '../secrets' } });
  assert.equal(bad.status, 404);
  const pending = await getData({ request: request('/api/data/photos', { as: 'pending' }), env: { ...ENV, PHOTOS: bucket }, params: { name: 'photos' } });
  assert.equal(pending.status, 403);
});

test('data route enforces the full name contract and authentication', async () => {
  const bucket = r2();
  const env = { ...ENV, PHOTOS: bucket };
  for (const name of ['photos', 'people', 'graph', 'questions', 'clusters']) {
    await bucket.put(`data/${name}.json`, new TextEncoder().encode('[1]'));
    for (const [options, status] of [[{}, 200], [{ headers: { authorization: '' } }, 401], [{ as: 'pending' }, 403]]) {
      const response = await getData({ request: request(`/api/data/${name}`, options), env, params: { name } });
      assert.equal(response.status, status, `${name}: ${status}`);
      if (status === 200) assert.equal(await response.text(), '[1]');
    }
  }
  for (const name of ['unknown', '../secrets', '..', 'photos/../../secrets', '%2e%2e%2fsecrets', 'photos.json']) {
    const response = await getData({ request: request(`/api/data/${encodeURIComponent(name)}`), env, params: { name } });
    assert.equal(response.status, 404, name);
  }
});

test('every admin route denies absent tokens and non-admin students', async () => {
  const key = 'pending/student-1/123-abc.jpg';
  const env = { ...ENV, PHOTOS: r2() };
  for (const [handler, path, options] of [
    [getUploads, '/api/admin/uploads', {}],
    [actOnUpload, '/api/admin/uploads', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key, action: 'approve' }) }],
    [getUploadImage, `/api/admin/upload-image?key=${encodeURIComponent(key)}`, {}],
  ]) {
    for (const [auth, status] of [[{ headers: { authorization: '' } }, 401], [{ as: 'pending' }, 403], [{ as: 'student-1' }, 403]]) {
      const response = await handler({ request: request(path, { ...options, ...auth, headers: { ...options.headers, ...auth.headers } }), env });
      assert.equal(response.status, status, `${options.method || 'GET'} ${path}: ${status}`);
    }
  }
});

test('review decisions write a marker and preserve the raw pending upload', async () => {
  const bucket = r2();
  const response = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ photo: 'p0123abcd', action: 'approve' }) }), env: { ...ENV, PHOTOS: bucket } });
  assert.equal(response.status, 200);
  const marker = JSON.parse(new TextDecoder().decode((await bucket.get('review/p0123abcd.json')).body));
  assert.deepEqual(Object.keys(marker).sort(), ['action', 'at', 'by']);
  assert.equal(marker.action, 'approve');
  assert.equal(marker.by, 'admin');
});

test('me reports profile and admin status; malformed or absent tokens fail closed', async () => {
  const me = await getMe({ request: request('/api/me'), env: ENV });
  assert.deepEqual(await me.json(), { ok: true, email: 'rkmv1234@gmail.com', regno: '1234', status: 'approved', admin: false });
  const admin = await getMe({ request: request('/api/me', { as: 'admin' }), env: ENV });
  assert.equal((await admin.json()).admin, true);
  const denied = await getMe({ request: new Request('https://gallery.test/api/me'), env: ENV });
  assert.equal(denied.status, 401);
  const invalid = await getMe({ request: request('/api/me', { jwt: token({ email: 'x@example.com' }) }), env: ENV });
  assert.equal(invalid.status, 401);
});

test('upload validates magic bytes and stores a random pending key', async () => {
  const bucket = r2();
  await bucket.put('state/catalog.json', new TextEncoder().encode('[]'));
  for (const [bytes, ext, mime] of [
    [[0xff, 0xd8, 0xff, 1], 'jpg', 'image/jpeg'],
    [[137, 80, 78, 71, 13, 10, 26, 10], 'png', 'image/png'],
    [[82, 73, 70, 70, 0, 0, 0, 0, 87, 69, 66, 80], 'webp', 'image/webp'],
  ]) {
    const form = new FormData();
    form.set('file', new Blob([Uint8Array.from(bytes)], { type: 'text/plain' }), 'fake.txt');
    const response = await postUpload({ request: request('/api/upload', { method: 'POST', body: form }), env: { ...ENV, PHOTOS: bucket } });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.ok, true);
    assert.match(body.key, new RegExp(`^pending/student-1/[a-f0-9]{64}/\\d+-[0-9a-f-]+\\.${ext}$`));
    assert.equal(objects.get(body.key).httpMetadata.contentType, mime);
  }

  const invalid = new FormData();
  invalid.set('file', new Blob(['not an image'], { type: 'image/jpeg' }), 'x.jpg');
  const rejected = await postUpload({ request: request('/api/upload', { method: 'POST', body: invalid }), env: { ...ENV, PHOTOS: bucket } });
  assert.equal(rejected.status, 400);
});

test('exact duplicate preflight checks the archive and this user\'s queued uploads', async () => {
  const bucket = r2();
  const archive = 'a'.repeat(64), queued = 'b'.repeat(64), fresh = 'c'.repeat(64);
  await bucket.put('state/catalog.json', new TextEncoder().encode(JSON.stringify([{ id: 'p0123abcd', upload_sha: archive }])));
  await bucket.put(`pending/student-1/${queued}/123-abc.jpg`, Uint8Array.from([0xff, 0xd8, 0xff]));
  const response = await checkUploads({
    request: request('/api/uploads/check', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ hashes: [archive, queued, fresh] }) }),
    env: { ...ENV, PHOTOS: bucket },
  });
  assert.deepEqual((await response.json()).items, [
    { sha: archive, state: 'archive' },
    { sha: queued, state: 'queued' },
    { sha: fresh, state: 'new' },
  ]);
  assert.equal((await checkUploads({ request: request('/api/uploads/check', { method: 'POST', as: 'pending', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ hashes: [fresh] }) }), env: { ...ENV, PHOTOS: bucket } })).status, 403);
  assert.equal((await checkUploads({ request: request('/api/uploads/check', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ hashes: ['bad'] }) }), env: { ...ENV, PHOTOS: bucket } })).status, 400);
});

test('upload rejects exact archive duplicates before writing a pending object', async () => {
  const bucket = r2();
  const bytes = Uint8Array.from([0xff, 0xd8, 0xff, 1]);
  const sha = createHash('sha256').update(bytes).digest('hex');
  await bucket.put('state/catalog.json', new TextEncoder().encode(JSON.stringify([{ id: 'p0123abcd', upload_sha: sha }])));
  const form = new FormData();
  form.set('file', new Blob([bytes], { type: 'image/jpeg' }), 'same.jpg');
  const response = await postUpload({ request: request('/api/upload', { method: 'POST', body: form }), env: { ...ENV, PHOTOS: bucket } });
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { ok: false, error: 'duplicate' });
  assert.equal((await bucket.list({ prefix: 'pending/' })).objects.length, 0);

  const queuedBucket = r2();
  await queuedBucket.put('state/catalog.json', new TextEncoder().encode('[]'));
  await queuedBucket.put(`pending/student-1/${sha}/123-abc.jpg`, bytes);
  const queuedForm = new FormData();
  queuedForm.set('file', new Blob([bytes], { type: 'image/jpeg' }), 'same.jpg');
  const queued = await postUpload({ request: request('/api/upload', { method: 'POST', body: queuedForm }), env: { ...ENV, PHOTOS: queuedBucket } });
  assert.equal(queued.status, 409);
  assert.deepEqual(await queued.json(), { ok: false, error: 'duplicate' });
  assert.equal((await queuedBucket.list({ prefix: `pending/student-1/${sha}/` })).objects.length, 1);
});

test('upload cutover hold rejects before touching the current store', async () => {
  let touched = false;
  const response = await postUpload({
    request: request('/api/upload', { method: 'POST' }),
    env: { ...ENV, UPLOAD_HOLD: true, PHOTOS: { getWithMetadata() {}, async list() { touched = true; return { keys: [] }; } } },
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { ok: false, error: 'upload_paused_for_storage_cutover' });
  assert.equal(touched, false);
});

test('upload rejects a streamed body over 10 MB with 413', async () => {
  const bytes = new Uint8Array(10 * 1024 * 1024 + 128 * 1024 + 1); // over the whole-request cap (file limit + multipart overhead)
  let sent = false;
  const body = new ReadableStream({ pull(controller) {
    if (!sent) { sent = true; controller.enqueue(bytes); }
    else controller.close();
  } });
  const req = new Request('https://gallery.test/api/upload', { method: 'POST', headers: { authorization: `Bearer ${token()}`, 'content-type': 'multipart/form-data; boundary=x' }, body, duplex: 'half' });
  const response = await postUpload({ request: req, env: { ...ENV, PHOTOS: r2() } });
  assert.equal(response.status, 413);
});

test('upload reports storage outages without exposing backend failures', async () => {
  const form = new FormData();
  form.set('file', new Blob([Uint8Array.from([0xff, 0xd8, 0xff, 1])], { type: 'image/jpeg' }), 'a.jpg');
  for (const photos of [
    { getWithMetadata() {}, async list() { throw new Error('private backend details'); } },
    { async get() { return { body: new TextEncoder().encode('[]') }; }, async list() { return { objects: [], truncated: false }; }, async put() { throw new Error('private backend details'); } },
  ]) {
    const response = await postUpload({ request: request('/api/upload', { method: 'POST', body: form }), env: { ...ENV, PHOTOS: photos } });
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { ok: false, error: 'storage_unavailable' });
  }
});

test('admin approves all currently reviewed uploads from one uploader with one marker', async () => {
  const bucket = r2();
  await bucket.put('pending/student-1/123-queued.jpg', Uint8Array.from([0xff, 0xd8, 0xff]));
  await bucket.put('data/pending.json', new TextEncoder().encode(JSON.stringify({ items: [
    { photo: 'p00000001', uploader: 'student-1' }, { photo: 'p00000002', uploader: 'student-1' },
    { photo: 'p00000003', uploader: 'student-2' },
  ] })));
  const response = await actOnUpload({
    request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ action: 'approve-user', uid: 'student-1' }) }),
    env: { ...ENV, PHOTOS: bucket },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, approved: 3, queued: 1 });
  const batches = await bucket.list({ prefix: 'review/batches/' });
  assert.equal(batches.objects.length, 1);
  const marker = JSON.parse(new TextDecoder().decode((await bucket.get(batches.objects[0].key)).body));
  assert.deepEqual(marker, { action: 'approve', photos: ['p00000001', 'p00000002'], uploadKeys: ['pending/student-1/123-queued.jpg'], by: 'admin', at: marker.at });
  assert.ok(Number.isFinite(Date.parse(marker.at)));
});

test('admin routes list and preview pending uploads; decisions use markers', async () => {
  const bucket = r2();
  const key = `pending/student-1/${Date.now()}-abc123.jpg`;
  await bucket.put(key, Uint8Array.from([0xff, 0xd8, 0xff]), { httpMetadata: { contentType: 'image/jpeg' } });
  await bucket.put('pending/student-1/123-queued.jpg', Uint8Array.from([0xff, 0xd8, 0xff]));
  const env = { ...ENV, PHOTOS: bucket };
  const list = await getUploads({ request: request('/api/admin/uploads', { as: 'admin' }), env });
  const listed = await list.json();
  assert.equal(listed.ok, true);
  assert.deepEqual(listed.items, []);
  assert.equal(listed.queued, 2);
  assert.deepEqual(new Set(listed.queuedItems.map(item => item.key)), new Set([key, 'pending/student-1/123-queued.jpg']));
  assert.equal((await getUploadImage({ request: request(`/api/admin/upload-image?key=${encodeURIComponent(key)}`, { as: 'admin' }), env })).status, 200);
  const approved = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ photo: 'p0123abcd', action: 'approve' }) }), env });
  assert.equal(approved.status, 200);
  assert.equal(objects.has(key), true);
  assert.equal(objects.has('review/p0123abcd.json'), true);
  const rejected = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ photo: 'p0123abce', action: 'reject' }) }), env });
  assert.equal(rejected.status, 200);
  assert.equal(objects.has('review/p0123abce.json'), true);
  const traversal = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ photo: '../secrets', action: 'approve' }) }), env });
  assert.equal(traversal.status, 400);
  const forbidden = await getUploads({ request: request('/api/admin/uploads'), env });
  assert.equal(forbidden.status, 403);
});

test('upload stops at 50 pending files per student', async () => {
  const bucket = r2();
  for (let i = 0; i < 50; i++) await bucket.put(`pending/student-1/${i}.jpg`, Uint8Array.from([0xff, 0xd8, 0xff]));
  const form = new FormData();
  form.set('file', new Blob([Uint8Array.from([0xff, 0xd8, 0xff, 1])]), 'a.jpg');
  const response = await postUpload({ request: request('/api/upload', { method: 'POST', body: form }), env: { ...ENV, PHOTOS: bucket } });
  assert.equal(response.status, 429);
});
