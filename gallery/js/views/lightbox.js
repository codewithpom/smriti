// Route-driven overlay (#/photo/<id>) rendered above whichever view is mounted underneath.
import { el, label, imgUrl, cssUrl, avatar, postJSON, SAVE_FAIL, icon, ICON } from '../util.js';
import { link } from '../filters.js';

let root = null, ui = null, ctx = null, cur = null, order = [], pos = 0, ro = null, prevFocus = null;
let showFaces = false, infoOpen = false, idleTimer = null, touchStart = null;
let bodyOverflow = '', shellInert = false;

export const isOpen = () => !!root;

export function open(id, c) {
  ctx = c;
  const initialOpen = !root;
  if (initialOpen) build();
  show(id);
  if (initialOpen && root) ui.close.focus({ preventScroll: true });
}

export function close() {
  if (!root) return;
  document.removeEventListener('keydown', onKey);
  clearTimeout(idleTimer);
  ro.disconnect();
  root.remove();
  root = ui = cur = null;
  document.body.style.overflow = bodyOverflow;
  const shell = document.querySelector('.shell');
  if (shell) shell.inert = shellInert;
  if (prevFocus?.isConnected) prevFocus.focus?.({ preventScroll: true });
  prevFocus = null;
  infoOpen = false;
  touchStart = null;
}

function control(name, drawing, action, extra = {}) {
  return el('button', { type: 'button', class: 'btn lb-control', 'aria-label': name,
    title: name, onclick: action, ...extra }, icon(drawing));
}

function build() {
  prevFocus = document.activeElement;
  bodyOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  ui = {};
  ui.frame = el('div', { class: 'lb-frame' });
  ui.prev = control('Previous photo', ICON.left, () => step(-1), { class: 'btn lb-control lb-arrow lb-prev' });
  ui.next = control('Next photo', ICON.right, () => step(1), { class: 'btn lb-control lb-arrow lb-next' });
  ui.stage = el('div', { class: 'lb-stage', ontouchstart: startTouch, ontouchend: endTouch,
    ontouchcancel: () => { touchStart = null; }, ondblclick: toggleZoom }, ui.frame, ui.prev, ui.next);
  ui.title = el('h1', { class: 'lb-title', tabindex: '-1' });
  ui.pos = el('span', { class: 'lb-position', role: 'status', 'aria-live': 'polite' });
  ui.close = control('Close photo', ICON.left, () => ctx.closeLightbox());
  ui.facesBtn = control('Show face outlines (F)', 'M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4', toggleFaces,
    { 'aria-pressed': 'false', 'aria-keyshortcuts': 'F' });
  ui.infoBtn = control('Photo information (I)', 'M8 7v5M8 4v.2M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1', () => toggleInfo(),
    { 'aria-expanded': 'false', 'aria-controls': 'lb-info', 'aria-keyshortcuts': 'I' });
  ui.bar = el('header', { class: 'lb-toolbar' }, ui.close, ui.title, ui.pos, ui.facesBtn, ui.infoBtn);
  ui.date = el('p');
  ui.scene = el('p');
  ui.alt = el('p', { class: 'lb-description' });
  ui.people = el('ul', { class: 'plain lb-people' });
  ui.unknown = el('p', { class: 'lb-unknown' });
  ui.tags = el('div', { class: 'chips' });
  ui.sel = el('select', { class: 'input', 'aria-label': 'Place' });
  ui.status = el('p', { class: 'muted small', role: 'status' });
  const fix = el('details', { class: 'lb-fix' },
    el('summary', { text: 'Where was this taken? Not right?' }),
    el('div', { class: 'lb-fix-body' }, ui.sel, el('button', { type: 'button', class: 'btn', onclick: saveScene }, 'Save'), ui.status));
  ui.peopleWrap = el('section', {}, el('h2', { text: 'People' }), ui.people, ui.unknown);
  ui.tagsWrap = el('section', {}, el('h2', { text: 'Tags' }), ui.tags);
  ui.panel = el('aside', { class: 'lb-panel', id: 'lb-info', 'aria-label': 'Photo information', 'aria-hidden': 'true', inert: true },
    el('div', { class: 'lb-head' }, el('h2', { text: 'Info' }),
      control('Close photo information', ICON.close, () => toggleInfo(false))),
    ui.date, ui.scene, ui.alt, ui.peopleWrap, ui.tagsWrap, fix);
  root = el('div', { class: 'lb gp-lightbox', role: 'dialog', 'aria-modal': 'true',
    'aria-label': 'Photo viewer', tabindex: '-1', onpointermove: activity, onpointerdown: activity,
    onfocusin: activity, onfocusout: activity }, ui.bar, ui.stage, ui.panel);
  document.body.append(root);
  const shell = document.querySelector('.shell');
  if (shell) { shellInert = shell.inert; shell.inert = true; }
  document.addEventListener('keydown', onKey);
  ro = new ResizeObserver(fit);
  ro.observe(ui.stage);
  activity();
}

function activity() {
  if (!root) return;
  root.classList.remove('lb-idle');
  clearTimeout(idleTimer);
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  idleTimer = setTimeout(() => {
    const active = document.activeElement;
    if (root && !(root.contains(active) && active?.closest?.('button,a,select,input,summary')))
      root.classList.add('lb-idle');
  }, 3000);
}

function toggleInfo(value = !infoOpen) {
  infoOpen = value;
  if (!value && ui.panel.contains(document.activeElement)) ui.infoBtn.focus();
  ui.panel.inert = !value;
  ui.panel.setAttribute('aria-hidden', String(!value));
  ui.infoBtn.setAttribute('aria-expanded', String(value));
  root.classList.toggle('lb-info-open', value);
  activity();
}

// Double-click or double-tap zooms the photo in about the tapped point and back out. While zoomed, swipes are ignored.
let zoomed = false, lastTap = 0, lastZoom = 0;
function toggleZoom(e) {
  const img = ui.frame.querySelector('img');
  if (!img || e.target.closest('a,button') || Date.now() - lastZoom < 400) return; // touch double-tap also fires dblclick
  lastZoom = Date.now();
  zoomed = !zoomed;
  const r = img.getBoundingClientRect();
  img.style.transformOrigin = `${((e.clientX - r.left) / r.width) * 100}% ${((e.clientY - r.top) / r.height) * 100}%`;
  img.style.transform = zoomed ? 'scale(2.4)' : '';
  img.classList.toggle('lb-zoomed', zoomed);
}
function resetZoom() { zoomed = false; }

function startTouch(e) {
  if (e.touches.length === 1 && Date.now() - lastTap < 300) { toggleZoom(e.touches[0] && { target: e.target, clientX: e.touches[0].clientX, clientY: e.touches[0].clientY }); lastTap = 0; touchStart = null; return; }
  if (e.touches.length === 1) lastTap = Date.now();
  touchStart = e.touches.length === 1 && !zoomed && !e.target.closest('a,button')
    ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
}

function endTouch(e) {
  const start = touchStart;
  touchStart = null;
  if (!start || e.touches.length || e.changedTouches.length !== 1) return;
  const dx = e.changedTouches[0].clientX - start.x, dy = e.changedTouches[0].clientY - start.y;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
  else if (dy > 50 && dy > Math.abs(dx) * 1.5) ctx.closeLightbox();
}

// Date-only values are local calendar dates, never invented midnight timestamps.
function photoDate(value) {
  if (typeof value !== 'string') return 'No date';
  const parts = /^(\d{4})-(\d{2})-(\d{2})(?:$|T| )/.exec(value);
  if (!parts) return 'No date';
  const [, y, m, d] = parts.map(Number);
  const calendar = new Date(0);
  calendar.setFullYear(y, m - 1, d);
  if (calendar.getFullYear() !== y || calendar.getMonth() !== m - 1 || calendar.getDate() !== d) return 'No date';
  const hasTime = /[T ](?:[01]\d|2[0-3]):[0-5]\d/.test(value);
  if (value.length !== 10 && !hasTime) return 'No date';
  const date = hasTime ? new Date(value) : calendar;
  if (!Number.isFinite(date.getTime())) return 'No date';
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
    + (hasTime ? ' at ' + date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '');
}

function peopleInfo(p) {
  const entries = new Map();
  let unknown = 0;
  for (const face of p.faces) {
    const named = face.status === 'high' || face.status === 'confirmed';
    const maybe = face.status === 'candidate';
    const person = (named || maybe) && ctx.store.peopleById.get(face.person);
    if (!person) { unknown++; continue; }
    const prior = entries.get(person.id);
    if (!prior || named) entries.set(person.id, { person, maybe: !named });
  }
  const chips = [...entries.values()].map(({ person, maybe }) => {
    const image = avatar(person, 'avatar-sm');
    image.setAttribute('width', '32'); image.setAttribute('height', '32');
    const name = person.name || (person.reg_no ? 'Reg ' + person.reg_no : person.id);
    return el('li', {}, el('a', { class: 'lb-person', href: '#/person/' + encodeURIComponent(person.id) },
      image, el('span', { text: (maybe ? 'Maybe: ' : '') + name })));
  });
  ui.people.replaceChildren(...chips);
  unknown += Math.max(0, (Number(p.n_faces) || 0) - p.faces.length);
  ui.unknown.textContent = unknown ? `${unknown} unrecognised face${unknown === 1 ? '' : 's'}` : '';
  ui.unknown.hidden = !unknown;
  ui.peopleWrap.hidden = !chips.length && !unknown;
}

function fit() {
  if (!cur || !ui) return;
  const a = (cur.w / cur.h) || (cur.tw / cur.th) || 1;
  const r = ui.stage.getBoundingClientRect();
  let w = r.width, h = w / a;
  if (h > r.height) { h = r.height; w = h * a; }
  ui.frame.style.width = w + 'px';
  ui.frame.style.height = h + 'px';
}

function facesLayer(p) {
  const layer = el('div', { class: 'faces', hidden: !showFaces });
  for (const f of p.faces) {
    if (!f.bbox) continue;
    const [x, y, w, h] = f.bbox;
    const named = f.status === 'confirmed' || f.status === 'high';
    if (!named && f.status !== 'candidate') continue;
    const style = { left: x * 100 + '%', top: y * 100 + '%', width: w * 100 + '%', height: h * 100 + '%' };
    const person = named && f.person ? ctx.store.peopleById.get(f.person) : null;
    if (person) {
      layer.append(el('a', { class: 'face', href: '#/person/' + encodeURIComponent(person.id), style },
        el('span', { class: 'face-label' + (y + h > 0.88 ? ' above' : ''), text: person.name })));
    } else if (!named) {
      layer.append(el('div', { class: 'face face-cand', style, 'aria-hidden': 'true' }));
    }
  }
  return layer;
}

function show(id) {
  const store = ctx.store;
  const p = store.byId.get(id);
  if (!p) { location.replace(ctx.base()); return; }
  order = store.list;
  pos = order.findIndex((x) => x.id === id);
  if (pos < 0) { order = store.photos; pos = order.indexOf(p); }
  cur = p;
  document.title = `${label(p.scene) || 'Photo'} photo - RKMV Gallery`;

  ui.faces = facesLayer(p);
  ui.frame.style.backgroundColor = p.color;
  ui.frame.style.backgroundImage = cssUrl(p.thumb);
  const image = el('img', { src: imgUrl(p.web), alt: p.alt || '', loading: 'eager', decoding: 'async',
    width: p.w || p.tw || 1, height: p.h || p.th || 1 });
  image.addEventListener('load', () => image.classList.add('lb-loaded'), { once: true });
  if (image.complete) image.classList.add('lb-loaded');
  resetZoom();
  ui.frame.replaceChildren(image, ui.faces);
  fit();

  ui.pos.textContent = `${pos + 1} of ${order.length}`;
  const date = photoDate(p.date);
  ui.title.textContent = date;
  ui.date.replaceChildren(p.date && date !== 'No date' ? el('a', { href: link({ date: [p.date] }), text: date }) : 'No date');
  ui.scene.replaceChildren(p.scene ? el('a', { href: link({ scene: [p.scene] }), text: label(p.scene) }) : '');
  ui.scene.hidden = !p.scene;
  ui.alt.textContent = p.alt || '';
  ui.alt.hidden = !p.alt;
  ui.facesBtn.setAttribute('aria-pressed', String(showFaces));
  peopleInfo(p);

  const tags = [...new Set([...p.activity, ...p.objects, ...p.mood])];
  ui.tags.replaceChildren(...tags.map((t) => el('a', { class: 'chip', href: link({ tag: [t] }), text: label(t) })));
  ui.tagsWrap.hidden = tags.length === 0;

  ui.sel.replaceChildren(...store.sceneIds.map((s) => el('option', { value: s, text: label(s), selected: s === p.scene })));
  ui.status.textContent = '';

  ui.prev.disabled = pos <= 0;
  ui.next.disabled = pos >= order.length - 1;
  for (const j of [pos - 1, pos + 1]) if (order[j]) new Image().src = imgUrl(order[j].web);

  // Keep keyboard navigation in place unless its control became disabled or was replaced.
  if (!root.contains(document.activeElement) || document.activeElement.disabled) ui.title.focus({ preventScroll: true });
  activity();
}

function step(d) {
  const n = order[pos + d];
  if (n) ctx.go('#/photo/' + encodeURIComponent(n.id), true);
}

function toggleFaces() {
  showFaces = !showFaces;
  ui.faces.hidden = !showFaces;
  ui.facesBtn.setAttribute('aria-pressed', String(showFaces));
}

async function saveScene() {
  const p = cur;
  ui.status.textContent = 'Saving...';
  const r = await postJSON('/answer', { qid: 's' + p.id, type: 'scene', scene: ui.sel.value });
  if (cur !== p || !ui) return;
  ui.status.textContent = r.ok ? 'Saved. It will show after the next update.' : SAVE_FAIL;
}

function onKey(e) {
  activity();
  if (e.key === 'Escape') { e.preventDefault(); if (infoOpen) toggleInfo(false); else ctx.closeLightbox(); return; }
  if (e.key === 'Tab') { trap(e); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest('select,input,textarea')) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  else if (!e.repeat && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); toggleFaces(); }
  else if (!e.repeat && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); toggleInfo(); }
}

function trap(e) {
  const f = [...root.querySelectorAll('a[href],button:not([disabled]),select,input,summary,[tabindex]:not([tabindex="-1"])')]
    .filter((n) => n.offsetParent !== null && !n.closest('[inert],[hidden]'));
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  const a = document.activeElement;
  if (!f.includes(a)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
}
