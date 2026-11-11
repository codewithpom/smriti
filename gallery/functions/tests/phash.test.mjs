import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { classify, findMatch, hamming, phashGray } from '../../js/phash.js';

test('Python-generated vectors have identical JS hashes', async () => {
  const vectors = JSON.parse(await readFile(new URL('../../pipeline/testdata/phash_vectors.json', import.meta.url), 'utf8'));
  for (const vector of vectors) {
    const h = vector.gray.length, w = vector.gray[0].length;
    const gray = Float64Array.from(vector.gray.flat());
    assert.equal(hamming(phashGray(gray, w, h), vector.hash), 0, vector.name);
  }
});

test('hamming and classification thresholds', () => {
  assert.equal(hamming('0000000000000000', '0000000000000007'), 3);
  const a = { sha: 'one', ph: '0'.repeat(16), w: 100, h: 100 };
  assert.equal(classify(a, { sha: 'two', ph: '000000000000003f', w: 100, h: 100 }), 'duplicate');
  assert.equal(classify(a, { sha: 'two', ph: '0000000000000fff', w: 100, h: 100 }), 'similar');
  assert.equal(classify(a, { sha: 'one', ph: 'f'.repeat(16), w: 200, h: 100 }), 'duplicate');
});

test('findMatch prefers duplicate and then lower Hamming distance', () => {
  const item = { sha: 'item', ph: '0'.repeat(16), w: 10, h: 10 };
  const index = [
    { id: 'similar', sha: 's', ph: '0000000000000fff', w: 10, h: 10 },
    { id: 'duplicate-first', sha: 'd1', ph: '000000000000003f', w: 10, h: 10 },
    { id: 'duplicate-best', sha: 'd2', ph: '0000000000000003', w: 10, h: 10 },
  ];
  assert.deepEqual(findMatch(item, index), ['duplicate', 'duplicate-best']);
});
