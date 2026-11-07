import assert from 'node:assert/strict';
import test from 'node:test';
import { storage } from '../_shared/store.js';

// In-memory stand-in for a Workers KV namespace (same method names and result shapes).
function fakeKv() {
  const map = new Map();
  return {
    async getWithMetadata(key) {
      const e = map.get(key);
      return e ? { value: e.value.slice(0), metadata: e.metadata } : { value: null, metadata: null };
    },
    async put(key, value, options = {}) {
      const bytes = value instanceof ArrayBuffer ? value : new Uint8Array(value).buffer;
      map.set(key, { value: bytes, metadata: options.metadata ?? null });
    },
    async list({ prefix = '' } = {}) {
      const keys = [...map.entries()].filter(([k]) => k.startsWith(prefix)).map(([name, e]) => ({ name, metadata: e.metadata }));
      return { keys, list_complete: true, cursor: undefined };
    },
    async delete(key) { map.delete(key); },
  };
}

test('KV adapter keeps bytes, content type and size through put, get, list, delete', async () => {
  const store = storage({ PHOTOS: fakeKv() });
  await store.put('pending/u1/a.jpg', Uint8Array.from([0xff, 0xd8, 0xff, 1]), { httpMetadata: { contentType: 'image/jpeg' } });
  const got = await store.get('pending/u1/a.jpg');
  assert.deepEqual([...new Uint8Array(got.body)], [0xff, 0xd8, 0xff, 1]);
  assert.equal(got.httpMetadata.contentType, 'image/jpeg');
  assert.equal(got.size, 4);
  const listed = await store.list({ prefix: 'pending/u1/' });
  assert.deepEqual(listed.objects, [{ key: 'pending/u1/a.jpg', size: 4 }]);
  assert.equal((await store.list({ prefix: 'pending/u2/' })).objects.length, 0);
  // approve-style copy: the ArrayBuffer from get() goes straight back into put()
  await store.put('inbox/x.jpg', got.body, { httpMetadata: got.httpMetadata });
  assert.equal((await store.get('inbox/x.jpg')).size, 4);
  await store.delete('pending/u1/a.jpg');
  assert.equal(await store.get('pending/u1/a.jpg'), null);
});

test('an R2-shaped bucket passes through untouched', () => {
  const r2 = { get() {}, put() {}, list() {}, delete() {} };
  assert.equal(storage({ PHOTOS: r2 }), r2);
});

test('missing binding fails loudly', () => {
  assert.throws(() => storage({}), /storage_not_bound/);
});
