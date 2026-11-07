import fs from 'node:fs';
import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { initializeTestEnvironment, assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { doc, getDoc, setDoc, updateDoc, serverTimestamp } from 'firebase/firestore';

const projectId = 'demo-rkmv-gallery';
let env;
before(async () => {
  env = await initializeTestEnvironment({ projectId, firestore: { rules: fs.readFileSync('../firestore.rules', 'utf8') } });
});
beforeEach(async () => {
  await env.clearFirestore(); // every test starts from the same fixtures
  await env.withSecurityRulesDisabled(async ctx => {
    const db = ctx.firestore();
    await setDoc(doc(db, 'roster/1234'), { pid: 'S1234', name: 'Fixture' });
    await setDoc(doc(db, 'users/approved'), { email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status: 'approved' });
    await setDoc(doc(db, 'users/pending'), { email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status: 'pending' });
    await setDoc(doc(db, 'votes/fq1__approved'), { qid: 'fq1', uid: 'approved', regno: '1234', answer: 'S5678', ts: serverTimestamp() });
  });
});
after(async () => env?.cleanup());

const auth = (uid, email, email_verified = true) => env.authenticatedContext(uid, { email, email_verified }).firestore();
const admin = () => env.authenticatedContext('admin', { email: 'codewithpom@gmail.com', email_verified: true }).firestore();
const vote = (uid, regno = '1234', qid = 'fq1') => ({ qid, qtype: 'face', uid, regno, answer: 'S1234', ts: serverTimestamp() });

test('valid verified student can read own roster; wrong pattern and unverified cannot', async () => {
  await assertSucceeds(getDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'roster/1234')));
  await assertFails(getDoc(doc(auth('bad', 'student1234@gmail.com'), 'roster/1234')));
  await assertFails(getDoc(doc(auth('unverified', 'rkmv1234@gmail.com', false), 'roster/1234')));
  // own regno path is readable even if the doc is absent: an empty snapshot, nothing leaks
  assert.equal((await assertSucceeds(getDoc(doc(auth('missing', 'rkmv9999@gmail.com'), 'roster/9999')))).exists(), false);
  await assertFails(getDoc(doc(auth('nosy', 'rkmv9999@gmail.com'), 'roster/1234')));
});
test('a roster student is approved on first sign-in; nobody else, and nobody can change status themselves', async () => {
  const db = auth('new', 'rkmv1234@gmail.com');
  const profile = (status) => ({ email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status, createdAt: new Date() });
  await assertFails(setDoc(doc(db, 'users/new'), profile('pending'))); // no waiting room: the only valid start state is approved
  await assertFails(setDoc(doc(db, 'users/new'), profile('blocked')));
  await assertSucceeds(setDoc(doc(db, 'users/new'), profile('approved')));
  await assertFails(updateDoc(doc(db, 'users/new'), { status: 'blocked' })); // students cannot touch their own status
  const stranger = auth('stranger', 'rkmv9999@gmail.com'); // valid pattern, not on the roster
  await assertFails(setDoc(doc(stranger, 'users/stranger'), { ...profile('approved'), email: 'rkmv9999@gmail.com', regno: '9999', pid: 'S9999' }));
});
test('admin can add a student to the roster and block a user; a blocked user cannot vote', async () => {
  await assertSucceeds(setDoc(doc(admin(), 'roster/5678'), { pid: 'S5678' }, { merge: true }));
  await assertFails(setDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'roster/5678'), { pid: 'S5678' }));
  await assertSucceeds(updateDoc(doc(admin(), 'users/approved'), { status: 'blocked' }));
  await assertFails(setDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'votes/fq2__approved'), vote('approved', '1234', 'fq2')));
});
test('pending user cannot vote; own votes require their uid and matching doc id', async () => {
  await assertFails(setDoc(doc(auth('pending', 'rkmv1234@gmail.com'), 'votes/fq1__pending'), vote('pending')));
  const db = auth('approved', 'rkmv1234@gmail.com');
  await assertSucceeds(setDoc(doc(db, 'votes/fq2__approved'), vote('approved', '1234', 'fq2')));
  await assertFails(setDoc(doc(db, 'votes/fq1__other'), vote('other')));
  await assertFails(setDoc(doc(db, 'votes/wrong__approved'), vote('approved')));
});
test('students read only own votes; progress writes are admin-only', async () => {
  await assertSucceeds(getDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'votes/fq1__approved')));
  await assertFails(getDoc(doc(auth('pending', 'rkmv1234@gmail.com'), 'votes/fq1__approved')));
  await assertFails(setDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'meta/progress'), { settled: {}, disputed: [], updatedAt: new Date() }));
  await assertSucceeds(setDoc(doc(admin(), 'meta/progress'), { settled: {}, disputed: [], updatedAt: new Date() }));
});
test('only admins can read and write overrides', async () => {
  await assertSucceeds(setDoc(doc(admin(), 'overrides/q'), { qid: 'q', answer: 'S1', adminEmail: 'codewithpom@gmail.com', ts: serverTimestamp() }));
  await assertSucceeds(getDoc(doc(admin(), 'overrides/q')));
  await assertFails(getDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'overrides/q')));
  await assertFails(setDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'overrides/q2'), { qid: 'q2', answer: 'S2', adminEmail: 'rkmv1234@gmail.com', ts: serverTimestamp() }));
});
test('only admins write the roster', async () => {
  await assertSucceeds(setDoc(doc(admin(), 'roster/4321'), { pid: 'S4321', name: 'Fixture Two' }));
  await assertFails(setDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'roster/4321'), { pid: 'S4321', name: 'x' }));
  await assertFails(setDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'roster/1234'), { pid: 'S1234', name: 'y' }));
});
test('vote ids must be well-formed and match the question type', async () => {
  const db = auth('approved', 'rkmv1234@gmail.com');
  await assertSucceeds(setDoc(doc(db, 'votes/c12__approved'), { ...vote('approved', '1234', 'c12'), qtype: 'cluster' }));
  await assertSucceeds(setDoc(doc(db, 'votes/sp21530c27__approved'), { ...vote('approved', '1234', 'sp21530c27'), qtype: 'scene' }));
  await assertFails(setDoc(doc(db, 'votes/junk__approved'), vote('approved', '1234', 'junk')));
  await assertFails(setDoc(doc(db, 'votes/c12__approved'), vote('approved', '1234', 'c12'))); // cluster id sent as a face
  const long = 'f' + 'x'.repeat(60);
  await assertFails(setDoc(doc(db, `votes/${long}__approved`), vote('approved', '1234', long)));
});
test('admin needs a verified email', async () => {
  const unverified = env.authenticatedContext('admin', { email: 'codewithpom@gmail.com', email_verified: false }).firestore();
  await assertFails(updateDoc(doc(unverified, 'users/pending'), { status: 'approved' }));
  await assertFails(getDoc(doc(unverified, 'overrides/q')));
});
