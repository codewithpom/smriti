// Route-driven overlay (#/photo/<id>) rendered above whichever view is mounted underneath.
import { el, label, fmtDate, imgUrl, cssUrl, avatar, postJSON, SAVE_FAIL, icon, ICON } from '../util.js';
import { link } from '../filters.js';

let root = null, ui = null, ctx = null, cur = null, order = [], pos = 0, ro = null, prevFocus = null;
let showFaces = false;

export const isOpen = () => !!root;

export function open(id, c) {
  ctx = c;
  if (!root) build();
  show(id);
}

export function close() {
  if (!root) return;
  const id = cur && cur.id;
  document.removeEventListener('keydown', onKey);
  ro.disconnect();
  root.remove();
  root = ui = cur = null;
  document.querySelector('.shell').inert = false;
  const tile = [...document.querySelectorAll('.tile')].find((a) => a.getAttribute('href') === '#/photo/' + encodeURIComponent(id));
  (tile || prevFocus)?.focus?.();
}

function build() {
  prevFocus = document.activeElement;
  ui = {};
  ui.frame = el('div', { class: 'lb-frame' });
  ui.prev = el('button', { type: 'button', class: 'btn lb-arrow lb-prev', 'aria-label': 'Previous photo', onclick: () => step(-1) }, icon(ICON.left));
  ui.next = el('button', { type: 'button', class: 'btn lb-arrow lb-next', 'aria-label': 'Next photo', onclick: () => step(1) }, icon(ICON.right));
  ui.stage = el('div', { class: 'lb-stage' }, ui.frame, ui.prev, ui.next);

  ui.pos = el('span', { class: 'muted small num' });
  ui.title = el('h1', { class: 'lb-title', tabindex: '-1' });
  ui.date = el('p', { class: 'muted' });
  ui.alt = el('p');
  ui.facesBtn = el('button', { type: 'button', class: 'btn', 'aria-pressed': 'false', onclick: toggleFaces }, 'Show faces', el('span', { class: 'muted small', text: 'F' }));
  ui.people = el('ul', { class: 'plain' });
  ui.tags = el('div', { class: 'chips' });
  ui.sel = el('select', { class: 'input', 'aria-label': 'Place' });
  ui.status = el('p', { class: 'muted small', role: 'status' });
  const fix = el('details', { class: 'lb-fix' },
    el('summary', { text: 'Where was this taken? Not right?' }),
    el('div', { class: 'lb-fix-body' }, ui.sel, el('button', { type: 'button', class: 'btn', onclick: saveScene }, 'Save'), ui.status));
  ui.peopleWrap = el('section', {}, el('h2', { text: 'People' }), ui.people);
  ui.tagsWrap = el('section', {}, el('h2', { text: 'Tags' }), ui.tags);

  const closeBtn = el('button', { type: 'button', class: 'btn lb-close', 'aria-label': 'Close photo', onclick: () => ctx.closeLightbox() }, icon(ICON.close));
  const panel = el('div', { class: 'lb-panel' },
    el('div', { class: 'lb-head' }, ui.pos, closeBtn),
    ui.title, ui.date, ui.alt, el('div', {}, ui.facesBtn), ui.peopleWrap, ui.tagsWrap, fix);

  root = el('div', { class: 'lb', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Photo viewer' }, ui.stage, panel);
  document.body.append(root);
  document.querySelector('.shell').inert = true;
  document.addEventListener('keydown', onKey);
  ro = new ResizeObserver(fit);
  ro.observe(ui.stage);
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
  ui.frame.replaceChildren(el('img', { src: imgUrl(p.web), alt: p.alt || '', decoding: 'async' }), ui.faces);
  fit();

  ui.pos.textContent = `${pos + 1} of ${order.length}`;
  ui.title.replaceChildren(p.scene ? el('a', { href: link({ scene: [p.scene] }), text: label(p.scene) }) : 'Photo');
  ui.date.replaceChildren(p.date ? el('a', { href: link({ date: [p.date] }), text: fmtDate(p.date) }) : 'No date');
  ui.alt.textContent = p.alt || '';
  ui.alt.hidden = !p.alt;
  ui.facesBtn.setAttribute('aria-pressed', String(showFaces));

  const ppl = p.people.map((pid) => store.peopleById.get(pid)).filter(Boolean);
  ui.people.replaceChildren(...ppl.map((q) => el('li', {},
    el('a', { class: 'row', href: '#/person/' + encodeURIComponent(q.id) }, avatar(q, 'avatar-sm'), el('span', { class: 'row-name', text: q.name })))));
  ui.peopleWrap.hidden = ppl.length === 0;

  const tags = [...new Set([...p.activity, ...p.objects, ...p.mood])];
  ui.tags.replaceChildren(...tags.map((t) => el('a', { class: 'chip', href: link({ tag: [t] }), text: label(t) })));
  ui.tagsWrap.hidden = tags.length === 0;

  ui.sel.replaceChildren(...store.sceneIds.map((s) => el('option', { value: s, text: label(s), selected: s === p.scene })));
  ui.status.textContent = '';

  ui.prev.disabled = pos <= 0;
  ui.next.disabled = pos >= order.length - 1;
  for (const j of [pos - 1, pos + 1]) if (order[j]) new Image().src = imgUrl(order[j].web);

  ui.title.focus({ preventScroll: true });
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
  if (e.key === 'Escape') { e.preventDefault(); ctx.closeLightbox(); return; }
  if (e.key === 'Tab') { trap(e); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest('select,input,textarea')) return;
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'f' || e.key === 'F') toggleFaces();
}

function trap(e) {
  const f = [...root.querySelectorAll('a[href],button:not([disabled]),select,input,summary,[tabindex]:not([tabindex="-1"])')]
    .filter((n) => n.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  const a = document.activeElement;
  if (!root.contains(a)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
}
