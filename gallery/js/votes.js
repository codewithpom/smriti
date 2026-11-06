// Vote writes and reads. A student reads meta/progress once, and their own votes only when the local cache is empty.
import { getApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';
import {
  getFirestore, doc, getDoc, getDocs, setDoc, deleteDoc, collection, query, where, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';
import './auth.js'; // makes sure the Firebase app is initialised before getApp()
import { ls } from './util.js';

const me = () => getAuth(getApp()).currentUser;
const db = () => getFirestore(getApp());
const voteId = (qid, uid) => `${qid}__${uid}`;
const cacheKey = (uid) => `rkmv.votes.${uid}`;
// Local cache of this user's answers {qid: answer}. null means "not loaded yet", so a partial cache is never trusted.
function cached(uid) {
  const v = ls.get(cacheKey(uid));
  try { return v == null ? null : JSON.parse(v); } catch { return null; }
}
const saveCache = (uid, map) => ls.set(cacheKey(uid), JSON.stringify(map));

// The signed-in user's answered set {qid: answer}. One query, and only when the cache is empty.
export async function loadMyVotes() {
  const u = me();
  if (!u) return {};
  const hit = cached(u.uid);
  if (hit) return hit;
  const snap = await getDocs(query(collection(db(), 'votes'), where('uid', '==', u.uid)));
  const map = {};
  snap.forEach((d) => { map[d.data().qid] = d.data().answer; });
  saveCache(u.uid, map);
  return map;
}

// The single meta/progress doc ({settled, disputed, updatedAt}). A missing doc means nothing is settled yet.
export async function loadProgress() {
  const snap = await getDoc(doc(db(), 'meta', 'progress'));
  return snap.exists() ? snap.data() : { settled: {}, disputed: [] };
}

// Writes or replaces the user's vote for question q. Never throws: {ok:false} on failure, like postJSON.
export async function submitVote(q, answer) {
  const u = me();
  if (!u || typeof answer !== 'string' || !answer || answer.length > 64) return { ok: false };
  const regno = (u.email.match(/^rkmv(\d+)@/) || [])[1];
  try {
    await setDoc(doc(db(), 'votes', voteId(q.id, u.uid)), {
      qid: q.id, qtype: q.type, uid: u.uid, regno, answer, ts: serverTimestamp(),
    });
    const map = cached(u.uid);
    if (map) { map[q.id] = answer; saveCache(u.uid, map); }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Removes the user's vote for qid (used by undo). Never throws.
export async function deleteVote(qid) {
  const u = me();
  if (!u) return { ok: false };
  try {
    await deleteDoc(doc(db(), 'votes', voteId(qid, u.uid)));
    const map = cached(u.uid);
    if (map) { delete map[qid]; saveCache(u.uid, map); }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}
