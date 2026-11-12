import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, verify as rsaVerify } from 'node:crypto';
import { firebaseStorage, resetFirebaseTokenCache } from '../_shared/gcs.js';
import { getJson, listAll, putJson, storage } from '../_shared/store.js';

const BUCKET = 'smriti-private-test.appspot.com';
const SCOPE = 'https://www.googleapis.com/auth/devstorage.read_write';
const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const publicPem = publicKey.export({ type: 'spki', format: 'pem' });
const PRIVATE_JSON = JSON.stringify({
  client_email: 'worker-test@smriti-gallery-test.iam.gserviceaccount.com',
  private_key: privateKey.export({ type: 'pkcs8', format: 'pem' }),
});
const ENV = { FIREBASE_STORAGE_BUCKET: BUCKET, GOOGLE_SERVICE_ACCOUNT_JSON: PRIVATE_JSON };

const decode = (part) => JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));

afterEach(() => resetFirebaseTokenCache());

test('private GCS adapter supports the R2-shaped API and uses a scoped service-account token', async () => {
  const objects = new Map();
  const calls = [];
  let listPages = 0;
  const fetcher = async (input, init = {}) => {
    const url = new URL(input);
    calls.push({ url, init });
    if (url.href === TOKEN_URL) {
      const form = new URLSearchParams(init.body);
      assert.equal(form.get('grant_type'), 'urn:ietf:params:oauth:grant-type:jwt-bearer');
      const [head, payload, signature] = form.get('assertion').split('.');
      assert.deepEqual(decode(head), { alg: 'RS256', typ: 'JWT' });
      const claims = decode(payload);
      assert.equal(claims.iss, 'worker-test@smriti-gallery-test.iam.gserviceaccount.com');
      assert.equal(claims.scope, SCOPE);
      assert.equal(claims.aud, TOKEN_URL);
      assert.equal(rsaVerify('RSA-SHA256', Buffer.from(`${head}.${payload}`), publicPem, Buffer.from(signature, 'base64url')), true);
      return Response.json({ access_token: 'short-lived-test-token', expires_in: 3600 });
    }
    assert.equal(init.headers.authorization, 'Bearer short-lived-test-token');
    assert.match(url.pathname, new RegExp(`/b/${BUCKET.replaceAll('.', '\\.')}/o`));

    if (url.pathname.startsWith('/upload/')) {
      assert.equal(url.searchParams.get('uploadType'), 'media');
      const key = url.searchParams.get('name');
      const body = new Uint8Array(await new Response(init.body).arrayBuffer());
      objects.set(key, { body, contentType: init.headers['content-type'] });
      return Response.json({ name: key });
    }
    if (init.method === 'DELETE') {
      const key = decodeURIComponent(url.pathname.split('/').at(-1));
      objects.delete(key);
      return new Response(null, { status: 204 });
    }
    if (url.searchParams.get('alt') === 'media') {
      const key = decodeURIComponent(url.pathname.split('/').at(-1));
      const object = objects.get(key);
      return object
        ? new Response(object.body, { headers: { 'content-type': object.contentType } })
        : new Response('', { status: 404 });
    }
    if (url.pathname.endsWith('/o')) {
      assert.equal(url.searchParams.get('prefix'), 'pending/user-1/');
      listPages += 1;
      if (!url.searchParams.has('pageToken')) {
        return Response.json({ items: [{ name: 'pending/user-1/a.jpg', size: '3' }], nextPageToken: 'page-2' });
      }
      assert.equal(url.searchParams.get('pageToken'), 'page-2');
      return Response.json({ items: [{ name: 'pending/user-1/b.jpg', size: '5' }] });
    }
    throw new Error('unexpected test request');
  };
  const store = firebaseStorage(ENV, { fetcher, now: () => 1_800_000_000_000 });

  await putJson(store, 'status/scan.json', { state: 'queued' });
  assert.deepEqual(await getJson(store, 'status/scan.json'), { state: 'queued' });
  const objectsList = await listAll(store, 'pending/user-1/');
  assert.deepEqual(objectsList, [
    { key: 'pending/user-1/a.jpg', size: 3 },
    { key: 'pending/user-1/b.jpg', size: 5 },
  ]);
  assert.equal(listPages, 2);
  await store.delete('pending/user-1/a.jpg');
  assert.ok(calls.some(({ init }) => init.method === 'DELETE'));
  assert.equal(calls.filter(({ url }) => url.href === TOKEN_URL).length, 1, 'token is cached for subsequent object calls');
});

test('GCS adapter rejects missing configuration without falling back to KV', async () => {
  assert.throws(() => firebaseStorage({}), { message: 'firebase_storage_not_configured' });
});

test('runtime Firebase selection takes precedence over a still-present PHOTOS binding', async () => {
  const realFetch = globalThis.fetch;
  resetFirebaseTokenCache();
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    const url = new URL(input);
    calls.push({ url, init });
    if (url.href === TOKEN_URL) return Response.json({ access_token: 'route-test-token', expires_in: 3600 });
    return Response.json({ name: url.searchParams.get('name') });
  };
  try {
    const env = {
      ...ENV,
      PHOTOS_BACKEND: 'firebase',
      PHOTOS: { getWithMetadata() { throw new Error('must not use KV'); } },
    };
    await storage(env).put('pending/user-1/photo.jpg', new Uint8Array([1, 2, 3]), {
      httpMetadata: { contentType: 'image/jpeg' },
    });
    assert.ok(calls.some(({ url }) => url.origin === 'https://storage.googleapis.com' && url.pathname.startsWith('/upload/')));
  } finally {
    globalThis.fetch = realFetch;
    resetFirebaseTokenCache();
  }
});
