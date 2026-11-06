import { getApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';
import {
  collection, doc, getDoc, getDocs, getFirestore, limit, orderBy, query, serverTimestamp, setDoc, startAfter,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

const db = getFirestore(getApp());

export async function listUsers() {
  const snapshot = await getDocs(collection(db, 'users'));
  return snapshot.docs.map((item) => ({ uid: item.id, ...item.data() }));
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
