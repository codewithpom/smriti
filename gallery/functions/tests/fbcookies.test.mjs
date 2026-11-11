import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign } from 'node:crypto';
import { resetJwksCache } from '../_shared/auth.js';
import { toNetscape } from '../_shared/cookies.js';
import { onRequestPost, onRequestGet, onRequestDelete } from '../api/admin/fb-cookies.js';
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'cookie-test' };
export const session = JSON.stringify(['c_user', 'xs'].map(name => ({ domain: '.facebook.com', name, value: `private-${name}`, secure: true, expirationDate: 1234.9 })));
export function fixture(upstream = () => { throw new Error('unexpected fetch'); }) {
  resetJwksCache();
  globalThis.fetch = async (url, init) => url.includes('googleapis.com/service_accounts/') ? Response.json({ keys: [jwk] }) : upstream(url, init);
  const objects = new Map();
  const env = { FIREBASE_PROJECT_ID: 'cookie-test', ADMIN_EMAILS: 'admin@example.com', PHOTOS: {
    async put(key, body, options) { objects.set(key, { body, size: Buffer.byteLength(body), uploaded: new Date('2026-01-01'), ...options }); },
    async get(key) { return objects.get(key) ?? null; },
    async delete(key) { objects.delete(key); },
  } };
  function request(method = 'GET', body, role = 'admin') {
    const headers = {};
    if (role !== 'anonymous') {
      const encode = data => Buffer.from(JSON.stringify(data)).toString('base64url');
      const payload = `${encode({ alg: 'RS256', kid: jwk.kid })}.${encode({ aud: env.FIREBASE_PROJECT_ID, iss: `https://securetoken.google.com/${env.FIREBASE_PROJECT_ID}`, sub: role, email: role === 'admin' ? 'admin@example.com' : 'rkmv1234@gmail.com', email_verified: true, exp: Math.floor(Date.now() / 1000) + 3600 })}`;
      headers.authorization = `Bearer ${payload}.${sign('RSA-SHA256', Buffer.from(payload), privateKey).toString('base64url')}`;
    }
    return new Request('https://gallery.test/api/admin/test', { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  }
  return { env, objects, request };
}
export function noStore(response) { assert.equal(response.headers.get('cache-control'), 'no-store'); return response; }

test('normalizes JSON and Netscape, drops non-facebook cookies, requires sessions', () => {
  const result = toNetscape(session);
  assert.deepEqual(toNetscape(JSON.parse(session)), result);
  assert.deepEqual(toNetscape(result.text.replace('.facebook.com', '#HttpOnly_.facebook.com')), result);
  assert.equal(result.text, '# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t1234\tc_user\tprivate-c_user\n.facebook.com\tTRUE\t/\tTRUE\t1234\txs\tprivate-xs\n');
  const extra = ['evilfacebook.com', 'facebook.com.evil.test', 'example.com'].map(domain => ({ domain, name: 'xs', value: 'discard' }));
  assert.deepEqual(toNetscape([...JSON.parse(session), ...extra]), result);
  for (const input of [[], extra, [JSON.parse(session)[0]]]) assert.throws(() => toNetscape(input), { message: 'missing_session_cookies' });
  const host = JSON.parse(session).map(c => ({ ...c, domain: 'facebook.com', secure: false, expirationDate: undefined }));
  assert.match(toNetscape(host).text, /facebook.com\tFALSE\t\/\tFALSE\t0/);
});
test('rejects oversized, malformed and injected cookies without value leakage', () => {
  for (const input of ['private-invalid', '["private-secret"', 'é'.repeat(32769), ...['value', 'name', 'path'].map(field => [{ domain: '.facebook.com', name: 'xs', value: 'private', [field]: 'private\nsecret' }])]) assert.throws(() => toNetscape(input), { message: 'invalid_cookies' });
});
test('cookie POST/GET/DELETE expose only metadata', async () => {
  const { env, objects, request } = fixture();
  assert.deepEqual(await noStore(await onRequestGet({ env, request: request() })).json(), { present: false, bytes: 0, updated: null, hasUrl: false });
  assert.deepEqual(await noStore(await onRequestPost({ env, request: request('POST', { cookies: session, pageUrl: 'https://www.facebook.com/school/photos/' }) })).json(), { ok: true, count: 2 });
  assert.equal(objects.get('secrets/fb-cookies.txt').httpMetadata.contentType, 'text/plain');
  const info = await noStore(await onRequestGet({ env, request: request() })).json();
  assert.deepEqual(info, { present: true, bytes: Buffer.byteLength(toNetscape(session).text), updated: '2026-01-01T00:00:00.000Z', hasUrl: true });
  assert.ok(!JSON.stringify(info).includes('private-'));
  delete objects.get('secrets/fb-cookies.txt').uploaded;
  assert.equal((await (await onRequestGet({ env, request: request() })).json()).updated, null);
  assert.deepEqual(await noStore(await onRequestDelete({ env, request: request('DELETE') })).json(), { ok: true });
  assert.equal(objects.size, 0);
});
test('cookie endpoints deny non-admins before storage or body parsing', async () => {
  const { env, request } = fixture();
  delete env.PHOTOS;
  for (const [handler, method] of [[onRequestGet, 'GET'], [onRequestPost, 'POST'], [onRequestDelete, 'DELETE']]) for (const [role, status] of [['anonymous', 401], ['student', 403]]) assert.equal(noStore(await handler({ env, request: request(method, undefined, role) })).status, status);
});
test('cookie validation precedes writes and storage errors stay generic', async () => {
  const { env, objects, request } = fixture();
  for (const body of [{ cookies: 'private-invalid' }, { cookies: session, pageUrl: 'https://evil.test/private' }, { cookies: session, pageUrl: 'https://facebook.com/school\n' }]) assert.equal(noStore(await onRequestPost({ env, request: request('POST', body) })).status, 400);
  assert.equal(objects.size, 0);
  for (const [handler, method, operation] of [[onRequestPost, 'POST', 'put'], [onRequestGet, 'GET', 'get'], [onRequestDelete, 'DELETE', 'delete']]) {
    const saved = env.PHOTOS[operation];
    env.PHOTOS[operation] = async () => { throw new Error('private-storage-details'); };
    const response = noStore(await handler({ env, request: request(method, method === 'POST' ? { cookies: session } : undefined) }));
    assert.equal(response.status, 500);
    assert.deepEqual(await response.json(), { ok: false, error: 'storage_error' });
    env.PHOTOS[operation] = saved;
  }
});
test('cookie storage supports KV', async () => {
  const { env, request } = fixture();
  const values = new Map();
  env.PHOTOS = {
    async put(key, body, { metadata }) { values.set(key, { value: new TextEncoder().encode(body).buffer, metadata }); },
    async getWithMetadata(key) { return values.get(key) ?? { value: null }; },
    async delete(key) { values.delete(key); },
  };
  assert.equal((await onRequestPost({ env, request: request('POST', { cookies: session }) })).status, 200);
  assert.deepEqual(await noStore(await onRequestGet({ env, request: request() })).json(), { present: true, bytes: Buffer.byteLength(toNetscape(session).text), updated: null, hasUrl: false });
  assert.equal((await onRequestDelete({ env, request: request('DELETE') })).status, 200);
  assert.equal(values.size, 0);
});
