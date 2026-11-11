import fs from 'node:fs';
import assert from 'node:assert/strict';
import { after, before, beforeEach, test } from 'node:test';
import { initializeTestEnvironment, assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { doc, getDoc, getDocs, setDoc, updateDoc, deleteDoc, collection, query, where, serverTimestamp, Timestamp } from 'firebase/firestore';

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
    await setDoc(doc(db, 'roster/0001'), { pid: 'S0001', name: 'Fixture' });
    await setDoc(doc(db, 'roster/0002'), { pid: 'S0002', name: 'Fixture' });
    await setDoc(doc(db, 'users/approved'), { email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status: 'approved' });
    await setDoc(doc(db, 'users/pending'), { email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status: 'pending' });
    await setDoc(doc(db, 'votes/fq1__approved'), { qid: 'fq1', uid: 'approved', regno: '1234', answer: 'S5678', ts: serverTimestamp() });
  });
});
after(async () => env?.cleanup());

const google = { sign_in_provider: 'google.com' }; // the emulator default is 'custom'
const auth = (uid, email, email_verified = true, firebase = google) => env.authenticatedContext(uid, { email, email_verified, firebase }).firestore();
const admin = () => env.authenticatedContext('admin', { email: 'codewithpom@gmail.com', email_verified: true, firebase: google }).firestore();
const setApproval = (approval) => env.withSecurityRulesDisabled(ctx => setDoc(doc(ctx.firestore(), 'meta/config'), { approval }));
const vote = (uid, regno = '1234', qid = 'fq1') => ({ qid, qtype: 'face', uid, regno, answer: 'S1234', ts: serverTimestamp() });

test('valid verified student can read own roster; wrong pattern and unverified cannot', async () => {
  await assertSucceeds(getDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'roster/1234')));
  await assertFails(getDoc(doc(auth('bad', 'student1234@gmail.com'), 'roster/1234')));
  await assertFails(getDoc(doc(auth('unverified', 'rkmv1234@gmail.com', false), 'roster/1234')));
  // own regno path is readable even if the doc is absent: an empty snapshot, nothing leaks
  assert.equal((await assertSucceeds(getDoc(doc(auth('missing', 'rkmv9999@gmail.com'), 'roster/9999')))).exists(), false);
  await assertFails(getDoc(doc(auth('nosy', 'rkmv9999@gmail.com'), 'roster/1234')));
});
const profile = (status, createdAt = serverTimestamp()) => ({ email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status, createdAt });
test('auto mode: a roster student is approved on first sign-in; nobody else, and nobody can change status themselves', async () => {
  const db = auth('new', 'rkmv1234@gmail.com');
  await assertFails(setDoc(doc(db, 'users/new'), profile('pending')));
  await assertFails(setDoc(doc(db, 'users/new'), profile('blocked')));
  await assertSucceeds(setDoc(doc(db, 'users/new'), profile('approved')));
  await assertFails(updateDoc(doc(db, 'users/new'), { status: 'blocked' })); // students cannot touch their own status
  await assertFails(updateDoc(doc(db, 'users/new'), { status: 'approved' }));
  const stranger = auth('stranger', 'rkmv9999@gmail.com'); // valid pattern, not on the roster
  await assertFails(setDoc(doc(stranger, 'users/stranger'), { ...profile('approved'), email: 'rkmv9999@gmail.com', regno: '9999', pid: 'S9999' }));
});
test('admin approval mode: first sign-in must be pending', async () => {
  await setApproval('admin');
  const db = auth('new', 'rkmv1234@gmail.com');
  await assertFails(setDoc(doc(db, 'users/new'), profile('approved')));
  await assertSucceeds(setDoc(doc(db, 'users/new'), profile('pending')));
  await assertFails(updateDoc(doc(db, 'users/new'), { status: 'approved' })); // only an admin approves
  await assertSucceeds(updateDoc(doc(admin(), 'users/new'), { status: 'approved' }));
});
test('approval mode set back to auto behaves like a missing config doc', async () => {
  await setApproval('auto');
  await assertSucceeds(setDoc(doc(auth('new', 'rkmv1234@gmail.com'), 'users/new'), profile('approved')));
});
test('first sign-in needs a server createdAt and a Google provider', async () => {
  await assertFails(setDoc(doc(auth('new', 'rkmv1234@gmail.com'), 'users/new'), profile('approved', new Date())));
  await assertFails(setDoc(doc(auth('new', 'rkmv1234@gmail.com', true, { sign_in_provider: 'password' }), 'users/new'), profile('approved')));
  await assertSucceeds(setDoc(doc(auth('new', 'rkmv1234@gmail.com'), 'users/new'), profile('approved')));
});
test('only admin writes meta/config, with a valid value; a valid student can read it', async () => {
  await assertFails(setDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'meta/config'), { approval: 'admin' }));
  await assertSucceeds(setDoc(doc(admin(), 'meta/config'), { approval: 'admin' }));
  await assertFails(setDoc(doc(admin(), 'meta/config'), { approval: 'x' }));
  await assertFails(setDoc(doc(admin(), 'meta/config'), { approval: 'auto', extra: 1 }));
  await assertSucceeds(getDoc(doc(auth('s', 'rkmv1234@gmail.com'), 'meta/config')));
  await assertFails(getDoc(doc(auth('bad', 'student1234@gmail.com'), 'meta/config')));
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
test('vote updates are throttled to one per 10 seconds', async () => {
  const db = auth('approved', 'rkmv1234@gmail.com');
  await assertFails(setDoc(doc(db, 'votes/fq1__approved'), vote('approved'))); // fixture vote was just written
  await env.withSecurityRulesDisabled(ctx => setDoc(doc(ctx.firestore(), 'votes/fq1__approved'),
    { qid: 'fq1', qtype: 'face', uid: 'approved', regno: '1234', answer: 'S5678', ts: Timestamp.fromMillis(Date.now() - 60000) }));
  await assertSucceeds(setDoc(doc(db, 'votes/fq1__approved'), vote('approved')));
});
test('votes need every key and a short plain answer', async () => {
  const db = auth('approved', 'rkmv1234@gmail.com');
  const { regno, ...noRegno } = vote('approved', '1234', 'fq2');
  await assertFails(setDoc(doc(db, 'votes/fq2__approved'), noRegno));
  await assertFails(setDoc(doc(db, 'votes/fq2__approved'), { ...vote('approved', '1234', 'fq2'), answer: 'S 1234' }));
  await assertFails(setDoc(doc(db, 'votes/fq2__approved'), { ...vote('approved', '1234', 'fq2'), answer: 'S'.repeat(65) }));
  await assertSucceeds(setDoc(doc(db, 'votes/fq2__approved'), { ...vote('approved', '1234', 'fq2'), answer: 'S'.repeat(64) }));
});
test('votes reject an extra key', async () => {
  await assertFails(setDoc(doc(auth('approved', 'rkmv1234@gmail.com'), 'votes/fq2__approved'), { ...vote('approved', '1234', 'fq2'), extra: 1 }));
});
test('admin needs a verified email', async () => {
  const unverified = env.authenticatedContext('admin', { email: 'codewithpom@gmail.com', email_verified: false }).firestore();
  await assertFails(updateDoc(doc(unverified, 'users/pending'), { status: 'approved' }));
  await assertFails(getDoc(doc(unverified, 'overrides/q')));
});

const extraPerson = () => ({ reg_no: '0001', name: 'Test One', batch: 'MB28', addedBy: 'approved', ts: serverTimestamp(), status: 'pending' });
const student = () => auth('approved', 'rkmv1234@gmail.com');

test('approved student creates and reads a new person, including an empty name', async () => {
  await assertSucceeds(setDoc(doc(student(), 'people_extra/S0001'), extraPerson()));
  await assertSucceeds(getDoc(doc(student(), 'people_extra/S0001')));
  await assertSucceeds(getDoc(doc(admin(), 'people_extra/S0001')));
  await assertSucceeds(setDoc(doc(student(), 'people_extra/S0002'), { ...extraPerson(), reg_no: '0002', name: '' }));
});

for (const [label, pid, patch] of [
  ['wrong pid pattern', 'X0001', {}],
  ['extra key', 'S0001', { extra: true }],
  ['long name', 'S0001', { name: 'x'.repeat(61) }],
  ['bad batch', 'S0001', { batch: 'MB2' }],
  ['mismatched registration', 'S0001', { reg_no: '0002' }],
  ['non-digit registration', 'S0001', { reg_no: 'oops' }],
  ['numeric registration', 'S0001', { reg_no: 1 }],
  ['non-string name', 'S0001', { name: null }],
  ['other writer', 'S0001', { addedBy: 'someone-else' }],
  ['fake timestamp', 'S0001', { ts: new Date(0) }],
  ['status approved', 'S0001', { status: 'approved' }],
  ['unknown status', 'S0001', { status: 'weird' }],
]) test(`new people reject ${label}`, async () => {
  await assertFails(setDoc(doc(student(), 'people_extra/' + pid), { ...extraPerson(), ...patch }));
});

test('new people need a roster doc for their registration', async () => {
  await assertFails(setDoc(doc(student(), 'people_extra/S0003'), { ...extraPerson(), reg_no: '0003' }));
  await assertSucceeds(setDoc(doc(student(), 'people_extra/S0001'), extraPerson()));
});

for (const state of ['pending', 'blocked', 'unauthenticated']) test(`${state} cannot create or read new people`, async () => {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    if (state === 'blocked') await setDoc(doc(db, 'users/blocked'), { status: 'blocked' });
    await setDoc(doc(db, 'people_extra/S0001'), extraPerson());
  });
  const db = state === 'unauthenticated' ? env.unauthenticatedContext().firestore() : auth(state, 'rkmv1234@gmail.com');
  await assertFails(setDoc(doc(db, 'people_extra/S0002'), { ...extraPerson(), reg_no: '0002', addedBy: state }));
  await assertFails(getDoc(doc(db, 'people_extra/S0001')));
});

test('only admin edits or deletes new people and updates retain shape and identity', async () => {
  await assertSucceeds(setDoc(doc(student(), 'people_extra/S0001'), extraPerson()));
  const ref = doc(student(), 'people_extra/S0001'), adminRef = doc(admin(), 'people_extra/S0001');
  await assertFails(updateDoc(ref, { name: 'Test Two' }));
  await assertFails(deleteDoc(ref));
  await assertSucceeds(updateDoc(adminRef, { name: 'Test Two' }));
  await assertFails(updateDoc(adminRef, { reg_no: '0002' }));
  await assertFails(updateDoc(adminRef, { unexpected: true }));
  await assertFails(updateDoc(adminRef, { name: 'x'.repeat(61) }));
  await assertFails(updateDoc(adminRef, { batch: 'wrong' }));
  await assertFails(updateDoc(adminRef, { addedBy: 'other' }));
  await assertFails(updateDoc(adminRef, { ts: new Date(0) }));
  await assertFails(setDoc(adminRef, { reg_no: '0001', name: 'Test Two', batch: 'MB28' }));
  await assertSucceeds(deleteDoc(adminRef));
});

test('new people must be created pending: no status or approved status fails', async () => {
  const { status, ...noStatus } = extraPerson();
  await assertFails(setDoc(doc(student(), 'people_extra/S0001'), noStatus));
  await assertFails(setDoc(doc(student(), 'people_extra/S0001'), { ...extraPerson(), status: 'approved' }));
  await assertSucceeds(setDoc(doc(student(), 'people_extra/S0001'), extraPerson()));
});

test('moderated reads: pending is visible to its adder and admin only; approved to every approved student', async () => {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    await setDoc(doc(db, 'users/other'), { email: 'rkmv1234@gmail.com', regno: '1234', pid: 'S1234', status: 'approved' });
    await setDoc(doc(db, 'people_extra/S0001'), extraPerson()); // pending, addedBy 'approved'
    await setDoc(doc(db, 'people_extra/S0002'), { ...extraPerson(), reg_no: '0002', status: 'approved' });
  });
  const other = auth('other', 'rkmv1234@gmail.com');
  await assertFails(getDoc(doc(other, 'people_extra/S0001')));
  await assertSucceeds(getDoc(doc(student(), 'people_extra/S0001')));
  await assertSucceeds(getDoc(doc(other, 'people_extra/S0002')));
  await assertSucceeds(getDoc(doc(admin(), 'people_extra/S0001')));
  const col = (db) => collection(db, 'people_extra');
  const snap = await assertSucceeds(getDocs(query(col(other), where('status', '==', 'approved'))));
  assert.deepEqual(snap.docs.map(d => d.id), ['S0002']);
  await assertSucceeds(getDocs(query(col(student()), where('addedBy', '==', 'approved'))));
  await assertFails(getDocs(query(col(other), where('addedBy', '==', 'approved')))); // someone else's pending doc is in range
  await assertFails(getDocs(col(other)));
  assert.equal((await assertSucceeds(getDocs(col(admin())))).size, 2);
});

test('admin approves a pending person and a legacy doc without status; students cannot change status', async () => {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    const { status, ...legacy } = extraPerson();
    await setDoc(doc(db, 'people_extra/S0001'), extraPerson());
    await setDoc(doc(db, 'people_extra/S0002'), { ...legacy, reg_no: '0002' });
  });
  await assertFails(updateDoc(doc(student(), 'people_extra/S0001'), { status: 'approved' }));
  await assertFails(updateDoc(doc(admin(), 'people_extra/S0001'), { status: 'weird' }));
  await assertSucceeds(updateDoc(doc(admin(), 'people_extra/S0001'), { status: 'approved' }));
  await assertSucceeds(updateDoc(doc(admin(), 'people_extra/S0001'), { status: 'pending' }));
  await assertSucceeds(updateDoc(doc(admin(), 'people_extra/S0002'), { status: 'approved' }));
});
