// Hash router, shell wiring (theme, menu, nav state, open-question count, sign-in gate).
import { store, load, onChange } from './store.js';
import { el, ls } from './util.js';
import { onUser, signIn, signOut, currentProfile, isAdmin } from './auth.js';
import * as lightbox from './views/lightbox.js';
import * as gallery from './views/gallery.js';
import * as people from './views/people.js';
import * as person from './views/person.js';
import * as friends from './views/friends.js';
import * as places from './views/places.js';
import * as review from './views/review.js';

const SITE = 'RKMV Gallery';
const views = { gallery, people, person, friends, places, review };
// Loaded on first visit, so the public pages do not download the admin and upload code.
const lazyViews = { admin: () => import('./views/admin.js'), upload: () => import('./views/upload.js') };
const navFor = { person: 'people' };
const REVIEW = location.hostname === '127.0.0.1'; // local review server: no sign-in gate
const view = document.getElementById('view');
const $ = (s) => document.querySelector(s);

let current = null; // {name, inst, hash} the view mounted under #view (also the base under the lightbox)
let fromBase = false; // lightbox was opened from the base view, so closing can use history.back()

const ctx = {
  store, parts: [], query: new URLSearchParams(),
  setList(list) { store.list = list; },
  // Change the URL without re-running the router (replaceState/pushState fire no hashchange).
  setHash(hash, replace = false) {
    history[replace ? 'replaceState' : 'pushState'](null, '', hash);
    if (current) current.hash = location.hash;
  },
  go(hash, replace = false) { if (replace) location.replace(hash); else location.hash = hash; },
  base: () => (current ? current.hash : '#/'),
  closeLightbox() { if (fromBase) history.back(); else location.replace(ctx.base()); },
};

function parse() {
  const h = location.hash.slice(1) || '/';
  const i = h.indexOf('?');
  const path = i < 0 ? h : h.slice(0, i);
  const parts = path.split('/').filter(Boolean).map((s) => { try { return decodeURIComponent(s); } catch { return s; } });
  return { parts, query: new URLSearchParams(i < 0 ? '' : h.slice(i + 1)) };
}

function setNav(name) {
  const key = navFor[name] || name;
  document.querySelectorAll('[data-route]').forEach((a) => {
    if (a.dataset.route === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

function mountView(name, mod = views[name]) {
  current?.inst?.unmount?.();
  view.replaceChildren();
  const inst = mod.mount(view, ctx) || {};
  current = { name, inst, hash: location.hash || '#/' };
  document.title = `${inst.title || name} - ${SITE}`;
  $('#topbar-title').textContent = inst.title || SITE;
  setNav(name);
  window.scrollTo(0, 0);
  view.querySelector('h1')?.setAttribute('tabindex', '-1');
  view.querySelector('h1')?.focus({ preventScroll: true });
}

function showError(e) {
  view.replaceChildren(
    el('h1', { text: 'Could not load the gallery', tabindex: '-1' }),
    el('p', { class: 'muted', text: 'The photo data is missing or this page was opened as a file. Serve the folder over HTTP and check that data/photos.json and data/people.json exist.' }),
    el('p', { class: 'muted small', text: String(e && e.message || e) }));
}

// A screen that is not a mounted view (sign-in, waiting, denied). Clears `current` so the next route remounts.
function renderScreen(title, ...kids) {
  current?.inst?.unmount?.();
  current = null;
  view.replaceChildren(el('h1', { text: title, tabindex: '-1' }), ...kids);
  document.title = `${title} - ${SITE}`;
  $('#topbar-title').textContent = SITE;
  setNav('');
  view.querySelector('h1').focus({ preventScroll: true });
}

// ---- access gate: signed out -> sign-in, pending/blocked/invalid -> message, ok -> the app
let access = null; // cached check, cleared on every sign-in/out and on "Check again"
function firstUser() {
  return new Promise((resolve) => { const off = onUser((u) => { off(); resolve(u); }); });
}
async function computeAccess() {
  try {
    const user = await firstUser();
    if (!user) return { state: 'signed-out' };
    const p = await currentProfile();
    if (!p) return { state: 'invalid' };
    if (p.status !== 'approved') return { state: p.status === 'blocked' ? 'blocked' : 'pending' };
    return { state: 'ok', admin: await isAdmin() };
  } catch (error) {
    return { state: 'error', error };
  }
}
function checkAccess() {
  if (REVIEW) return Promise.resolve({ state: 'ok', admin: false });
  if (!access) {
    access = computeAccess().then((a) => { if (a.state === 'error') access = null; return a; });
  }
  return access;
}

// Nav links and sign-out button follow the access state (elements are cloned for the menu, so match all).
function setRoles(acc) {
  document.querySelectorAll('[data-role="member"]').forEach((e) => { e.hidden = acc.state !== 'ok'; });
  document.querySelectorAll('[data-role="admin"]').forEach((e) => { e.hidden = !acc.admin; });
  document.querySelectorAll('[data-sign-out]').forEach((e) => { e.hidden = acc.state === 'signed-out'; });
}

function showGate(acc) {
  const back = () => { access = null; route(); };
  const out = el('button', { type: 'button', class: 'btn', onclick: () => signOut() }, 'Sign out');
  if (acc.state === 'signed-out') {
    const msg = el('p', { class: 'rv-msg small', role: 'status' });
    renderScreen('Sign in to see the gallery',
      el('p', { class: 'muted', text: 'Sign in with the Google account that matches your registration number. Only rkmv<registration number>@gmail.com accounts can sign in.' }),
      el('div', { class: 'rv-actions' }, el('button', { type: 'button', class: 'btn btn-primary', onclick: () => signIn().catch(() => { msg.textContent = 'Sign-in did not finish. Try again.'; }) }, 'Sign in with Google')),
      msg);
  } else if (acc.state === 'pending') {
    renderScreen('Waiting for approval',
      el('p', { class: 'muted', text: 'Your account is registered. An admin still needs to approve it before you can see the gallery. Check again later.' }),
      el('div', { class: 'rv-actions' }, el('button', { type: 'button', class: 'btn btn-primary', onclick: back }, 'Check again'), out));
  } else if (acc.state === 'blocked') {
    renderScreen('Access is blocked',
      el('p', { class: 'muted', text: 'This account cannot see the gallery. Ask an admin if you think this is a mistake.' }),
      el('div', { class: 'rv-actions' }, out));
  } else if (acc.state === 'invalid') {
    renderScreen('This account cannot sign in',
      el('p', { class: 'muted', text: 'Only rkmv<registration number>@gmail.com accounts on the class roster can sign in. Sign out and use the right account.' }),
      el('div', { class: 'rv-actions' }, out));
  } else {
    renderScreen('Could not check your access',
      el('p', { class: 'muted', text: 'The sign-in check failed. Check your connection and try again.' }),
      el('div', { class: 'rv-actions' }, el('button', { type: 'button', class: 'btn btn-primary', onclick: back }, 'Try again'), out));
  }
}

async function route() {
  const acc = await checkAccess();
  setRoles(acc);
  if (acc.state !== 'ok') { showGate(acc); return; }
  try { await load(); } catch (e) { showError(e); return; }
  const { parts, query } = parse();
  Object.assign(ctx, { parts, query });
  const name = parts[0] || 'gallery';
  closeMenu();

  if (name === 'photo') {
    if (!current) { mountView('gallery'); current.hash = '#/'; }
    else if (!lightbox.isOpen()) fromBase = true;
    lightbox.open(parts[1], ctx);
    return;
  }
  const wasOpen = lightbox.isOpen();
  if (wasOpen) lightbox.close();
  fromBase = false;
  const key = name in views || name in lazyViews ? name : 'gallery';
  if (key === 'admin' && !acc.admin) { renderScreen('Admins only', el('p', { class: 'muted', text: 'Only admins can open this page.' })); return; }
  if (current && current.name === key && current.hash === location.hash) {
    // Back from the lightbox to the same base view: nothing to remount.
    document.title = `${current.inst.title || key} - ${SITE}`;
    setNav(key);
    return;
  }
  if (current && current.name === key && key === 'gallery' && current.inst.update) {
    current.hash = location.hash;
    current.inst.update(query);
    document.title = `Gallery - ${SITE}`;
    return;
  }
  let mod;
  try { mod = views[key] || await lazyViews[key](); } catch (e) { showError(e); return; }
  mountView(key, mod);
}

// ---- open-question count
function showCount() {
  const n = store.questions.length;
  document.querySelectorAll('[data-count]').forEach((c) => {
    c.hidden = n === 0;
    c.querySelector('[data-count-n]').textContent = n;
  });
}

// ---- theme
const THEMES = ['system', 'light', 'dark'];
function applyTheme(t) {
  const root = document.documentElement;
  if (t === 'light' || t === 'dark') root.dataset.theme = t; else delete root.dataset.theme;
  document.querySelectorAll('[data-theme-label]').forEach((s) => { s.textContent = t.charAt(0).toUpperCase() + t.slice(1); });
  window.dispatchEvent(new Event('themechange'));
}
let theme = THEMES.includes(ls.get('rkmv.theme')) ? ls.get('rkmv.theme') : 'system';

// ---- mobile menu
const menu = $('#menu'), menuBtn = $('#menu-btn');
function openMenu() { menu.hidden = false; menuBtn.setAttribute('aria-expanded', 'true'); menu.querySelector('a')?.focus(); }
function closeMenu() { if (menu.hidden) return; menu.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); }

function init() {
  $('#menu-nav').replaceChildren(...[...$('#nav').children].map((c) => c.cloneNode(true)));
  $('#menu-foot').replaceChildren(...[...$('#side-foot').children].map((c) => c.cloneNode(true)));
  menuBtn.addEventListener('click', () => (menu.hidden ? openMenu() : closeMenu()));
  $('#menu-close').addEventListener('click', () => { closeMenu(); menuBtn.focus(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !menu.hidden) { closeMenu(); menuBtn.focus(); } });
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-sign-out]')) { signOut(); return; }
    if (!e.target.closest('[data-theme-toggle]')) return;
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    ls.set('rkmv.theme', theme);
    applyTheme(theme);
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => window.dispatchEvent(new Event('themechange')));
  applyTheme(theme);
  onChange(showCount);
  window.addEventListener('hashchange', route);
  if (REVIEW) { route().then(showCount); return; }
  // Fires once on load and again on every sign-in or sign-out.
  onUser(() => { access = null; route().then(showCount); });
}
init();
