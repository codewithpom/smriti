import test, { before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign as rsaSign } from 'node:crypto';
import { resetJwksCache } from '../_shared/auth.js';
import { onRequestGet as getData } from '../api/data/[name].js';
import { onRequestGet as getMe } from '../api/me.js';
import { onRequestPost as postUpload } from '../api/upload.js';
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
    assert.match(body.key, new RegExp(`^pending/student-1/\\d+-[0-9a-f-]+\\.${ext}$`));
    assert.equal(objects.get(body.key).httpMetadata.contentType, mime);
  }

  const invalid = new FormData();
  invalid.set('file', new Blob(['not an image'], { type: 'image/jpeg' }), 'x.jpg');
  const rejected = await postUpload({ request: request('/api/upload', { method: 'POST', body: invalid }), env: { ...ENV, PHOTOS: bucket } });
  assert.equal(rejected.status, 400);
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

test('admin routes list, preview, approve and reject only pending uploads', async () => {
  const bucket = r2();
  const key = `pending/student-1/${Date.now()}-abc123.jpg`;
  await bucket.put(key, Uint8Array.from([0xff, 0xd8, 0xff]), { httpMetadata: { contentType: 'image/jpeg' } });
  const env = { ...ENV, PHOTOS: bucket };
  const list = await getUploads({ request: request('/api/admin/uploads', { as: 'admin' }), env });
  assert.equal((await list.json()).uploads[0].uid, 'student-1');
  assert.equal((await getUploadImage({ request: request(`/api/admin/upload-image?key=${encodeURIComponent(key)}`, { as: 'admin' }), env })).status, 200);
  const approved = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key, action: 'approve' }) }), env });
  assert.equal(approved.status, 200);
  assert.equal(objects.has(key), false);
  assert.equal([...objects.keys()].some(value => value.startsWith('inbox/')), true);
  const rejectKey = `pending/student-1/${Date.now()}-def456.png`;
  await bucket.put(rejectKey, new Uint8Array([1]));
  const rejected = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key: rejectKey, action: 'reject' }) }), env });
  assert.equal(rejected.status, 200);
  assert.equal(objects.has(rejectKey), false);
  const traversal = await actOnUpload({ request: request('/api/admin/uploads', { method: 'POST', as: 'admin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key: 'pending/../secrets', action: 'approve' }) }), env });
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
