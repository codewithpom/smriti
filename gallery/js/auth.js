// Google sign-in, student identity, approval status and admin check. Firebase modular SDK from pinned gstatic URLs.
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import {
  getAuth, onAuthStateChanged, signInWithPopup, signOut as fbSignOut, GoogleAuthProvider,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';
import {
  getFirestore, doc, getDoc, setDoc, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';
import { firebaseConfig } from './firebase-config.js';
import { API } from './config.js';

const app = initializeApp(firebaseConfig); // other modules reach the same app via getApp()
const auth = getAuth(app);
const db = getFirestore(app);
const STUDENT = /^rkmv(\d+)@gmail\.com$/;

export function onUser(cb) {
  return onAuthStateChanged(auth, cb); // returns the unsubscribe function
}

export function signIn() {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: 'select_account' });
  return signInWithPopup(auth, provider);
}

export function signOut() {
  return fbSignOut(auth);
}

// Fresh Firebase ID token for the signed-in user, or null. Sent as the Bearer token on /api/* calls.
export async function getToken() {
  const u = auth.currentUser;
  return u ? u.getIdToken() : null;
}

// {uid, status, ...} from users/{uid}, creating it on first sign-in (roster membership is the gate).
// New users start 'pending' while meta/config says approval is 'admin', otherwise 'approved'.
// Returns null when the account is not a verified rkmv<digits>@gmail.com student on the roster.
// Not cached: the sign-in gate re-reads it on "Check again", so an approval shows up without a reload.
export async function currentProfile() {
  const u = auth.currentUser;
  const m = u && u.emailVerified && STUDENT.exec((u.email || '').toLowerCase());
  if (!m) return null;
  const ref = doc(db, 'users', u.uid);
  const snap = await getDoc(ref);
  if (snap.exists()) return { uid: u.uid, ...snap.data() };
  const regno = m[1];
  const roster = await getDoc(doc(db, 'roster', regno));
  if (!roster.exists()) return null;
  let status = 'approved';
  try {
    const config = await getDoc(doc(db, 'meta', 'config'));
    if (config.exists() && config.data().approval === 'admin') status = 'pending';
  } catch { /* unreadable config counts as automatic approval */ }
  const profile = { email: u.email, regno, pid: roster.data().pid, status, createdAt: serverTimestamp() };
  await setDoc(ref, profile);
  return { uid: u.uid, ...profile };
}

// Asks the Function (GET /api/me) whether the signed-in user is an admin. Any failure means not admin.
export async function isAdmin() {
  const token = await getToken();
  if (!token) return false;
  try {
    const r = await fetch(`${API}/me`, { headers: { authorization: `Bearer ${token}` } });
    const j = await r.json().catch(() => ({}));
    return r.ok && j.ok === true && j.admin === true;
  } catch {
    return false;
  }
}
