// Hash router, shell wiring (theme, menu, nav state, open-question count).
import { store, load, onChange } from './store.js';
import { el, ls } from './util.js';
import * as lightbox from './views/lightbox.js';
import * as gallery from './views/gallery.js';
import * as people from './views/people.js';
import * as person from './views/person.js';
import * as friends from './views/friends.js';
import * as places from './views/places.js';
import * as review from './views/review.js';

const SITE = 'RKMV Gallery';
const views = { gallery, people, person, friends, places, review };
const navFor = { person: 'people' };
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

function mountView(name) {
  current?.inst?.unmount?.();
  view.replaceChildren();
  const inst = views[name].mount(view, ctx) || {};
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

async function route() {
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
  const key = name in views ? name : 'gallery';
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
  mountView(key);
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
    if (!e.target.closest('[data-theme-toggle]')) return;
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    ls.set('rkmv.theme', theme);
    applyTheme(theme);
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => window.dispatchEvent(new Event('themechange')));
  applyTheme(theme);
  onChange(showCount);
  window.addEventListener('hashchange', route);
  route().then(showCount);
}
init();
