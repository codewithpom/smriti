import { getApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';
import {
  collection, deleteDoc, doc, getDoc, getDocs, getFirestore, limit, orderBy, query, serverTimestamp, setDoc, startAfter, writeBatch,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

const db = getFirestore(getApp());

export async function listUsers() {
  const snapshot = await getDocs(collection(db, 'users'));
  return snapshot.docs.map((item) => ({ uid: item.id, ...item.data() }));
}

// Admin only: one roster doc per registration number lets that student register. 400 writes per batch (limit is 500).
export async function seedRoster(people) {
  const students = people.filter((p) => p.kind === 'student' && p.reg_no);
  for (let i = 0; i < students.length; i += 400) {
    const batch = writeBatch(db);
    students.slice(i, i + 400).forEach((p) => batch.set(doc(db, 'roster', String(p.reg_no)), { pid: p.id, name: p.name }));
    await batch.commit();
  }
  return students.length;
}

// Admin only: let extra students in. Takes pasted text (commas, spaces or lines), keeps only rkmv<digits>@gmail.com.
// pid is S<regno>, the same shape the pipeline uses; merge keeps the name and pid of students already imported.
export async function addRoster(text) {
  const found = String(text).toLowerCase().split(/[\s,;]+/).filter(Boolean);
  const regnos = [...new Set(found.map((s) => /^rkmv(\d+)@gmail\.com$/.exec(s)?.[1]).filter(Boolean))];
  for (let i = 0; i < regnos.length; i += 400) {
    const batch = writeBatch(db);
    regnos.slice(i, i + 400).forEach((r) => batch.set(doc(db, 'roster', r), { pid: `S${r}` }, { merge: true }));
    await batch.commit();
  }
  return { added: regnos.length, skipped: found.length - found.filter((s) => /^rkmv\d+@gmail\.com$/.test(s)).length };
}

export async function approveUsers(uids) {
  for (let i = 0; i < uids.length; i += 400) {
    const batch = writeBatch(db);
    uids.slice(i, i + 400).forEach((uid) => batch.update(doc(db, 'users', uid), { status: 'approved' }));
    await batch.commit();
  }
}

export function setUserStatus(uid, status) {
  if (!['approved', 'blocked'].includes(status)) throw new Error('Invalid user status.');
  return setDoc(doc(db, 'users', uid), { status }, { merge: true });
}

// Admin only: a full scan can consume the Firestore Spark budget of 50,000 reads/day.
export async function listAllVotes() {
  const votes = [];
  let cursor;
  while (true) {
    const page = await getDocs(query(collection(db, 'votes'), orderBy('__name__'), ...(cursor ? [startAfter(cursor)] : []), limit(500)));
    votes.push(...page.docs.map((item) => item.data()));
    if (page.size < 500) return votes;
    cursor = page.docs.at(-1);
  }
}

// 'admin' holds new students as pending; a missing doc means 'auto'.
export async function getApprovalMode() {
  const snapshot = await getDoc(doc(db, 'meta', 'config'));
  return snapshot.exists() && snapshot.data().approval === 'admin' ? 'admin' : 'auto';
}

export function setApprovalMode(mode) {
  if (!['auto', 'admin'].includes(mode)) throw new Error('Invalid approval mode.');
  return setDoc(doc(db, 'meta', 'config'), { approval: mode });
}

export function saveProgress({ settled, disputed }) {
  return setDoc(doc(db, 'meta', 'progress'), { settled, disputed, updatedAt: serverTimestamp() });
}

export async function getProgress() {
  const snapshot = await getDoc(doc(db, 'meta', 'progress'));
  return snapshot.exists() ? snapshot.data() : { settled: {}, disputed: [] };
}

export function saveOverride(qid, answer) {
  if (!qid || typeof answer !== 'string' || !answer.length || answer.length > 64) throw new Error('A question and answer of 1–64 characters are required.');
  return setDoc(doc(db, 'overrides', qid), { qid, answer, adminEmail: getAuth(getApp()).currentUser.email, ts: serverTimestamp() });
}

export async function listOverrides() {
  const snapshot = await getDocs(collection(db, 'overrides'));
  return Object.fromEntries(snapshot.docs.map((item) => [item.id, item.data().answer]));
}

export async function listExtraPeople() {
  const snapshot = await getDocs(collection(db, 'people_extra'));
  return snapshot.docs.map((item) => ({ ...item.data(), id: item.id }));
}

export function approveExtraPerson(pid) {
  return setDoc(doc(db, 'people_extra', pid), { status: 'approved' }, { merge: true });
}

export function updateExtraPerson(pid, name) {
  if (typeof name !== 'string' || name.length > 60) throw new Error('Names must be at most 60 characters.');
  return setDoc(doc(db, 'people_extra', pid), { name }, { merge: true });
}

export function deleteExtraPerson(pid) {
  return deleteDoc(doc(db, 'people_extra', pid));
}
