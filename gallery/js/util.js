import { IMAGE_BASE, API } from './config.js';

// DOM builder. Text goes in through text nodes only, never innerHTML, so data cannot inject markup.
export function add(n, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return n;
}

export function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'style') Object.assign(n.style, v);
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'hidden' || k === 'disabled' || k === 'selected') n[k] = v;
    else n.setAttribute(k, v === true ? '' : v);
  }
  return add(n, kids);
}

export const imgUrl = (p) => IMAGE_BASE + p;
export const cssUrl = (p) => `url("${imgUrl(p).replace(/["\\\n\r]/g, encodeURIComponent)}")`;
export const label = (s) => {
  s = String(s ?? '').replace(/-/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
};
export const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

export function fmtDate(iso) {
  if (!iso) return 'No date';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function debounce(fn, ms) {
  let t;
  const f = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  f.cancel = () => clearTimeout(t);
  return f;
}

export const ls = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

// Never throws. {ok:false} for network error, 404 or any non-ok body (static hosting).
export async function postJSON(path, body) {
  try {
    const r = await fetch(API + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, data };
    return data && data.ok ? { ok: true, data } : { ok: false, data };
  } catch { return { ok: false }; }
}
export const SAVE_FAIL = 'Answers can only be saved when run with the review server.';

const NS = 'http://www.w3.org/2000/svg';
export const ICON = { left: 'M10 3 5 8l5 5', right: 'M6 3l5 5-5 5', close: 'M3.5 3.5l9 9M12.5 3.5l-9 9' };
export function icon(d) {
  const s = document.createElementNS(NS, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 16 16', width: 16, height: 16, 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) s.setAttribute(k, v);
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', d);
  s.append(p);
  return s;
}

export function avatar(person, cls = '') {
  const initials = (person.name || '').trim().split(/\s+/).slice(0, 2).map((s) => s[0]).join('').toUpperCase() || '?';
  const fallback = () => el('span', { class: 'avatar ' + cls, 'aria-hidden': 'true', style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--raised)', color: 'var(--muted)', borderRadius: '50%' } }, initials);
  if (!person.avatar) return fallback();
  const image = el('img', { class: 'avatar ' + cls, src: imgUrl(person.avatar), alt: '', loading: 'lazy', decoding: 'async' });
  image.addEventListener('error', () => image.replaceWith(fallback()), { once: true });
  return image;
}

// Square crop around a normalised bbox (padded 25% each side), drawn from the web image with CSS only.
export function faceCrop(photo, bbox, size) {
  const [x, y, w, h] = bbox;
  const W = photo.w, H = photo.h;
  const side = Math.max(w * W, h * H) * 1.5 || 1;
  const k = size / side;
  const cx = (x + w / 2) * W, cy = (y + h / 2) * H;
  return el('div', {
    class: 'crop', 'aria-hidden': 'true',
    style: {
      width: size + 'px', height: size + 'px', backgroundColor: photo.color,
      backgroundImage: cssUrl(photo.web), backgroundSize: `${W * k}px ${H * k}px`,
      backgroundPosition: `${-(cx - side / 2) * k}px ${-(cy - side / 2) * k}px`,
    },
  });
}
