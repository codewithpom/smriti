// Collect photos until the next would exceed the target row width, then justify.
// Keep a sparse final row at target height; use thumbnail ratios with safe fallbacks.
// Resize only updates tile geometry: links, images and focus remain intact.
import { el, imgUrl, debounce } from './util.js';

function ratio(p) {
  for (const [w, h] of [[p.tw, p.th], [p.w, p.h]]) {
    const r = Number(w) / Number(h);
    if (Number(w) > 0 && Number(h) > 0 && Number.isFinite(r) && r > 0) return r;
  }
  return 1.5;
}

function photoDate(value) {
  if (typeof value !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:$|T| )/.exec(value);
  if (!m) return null;
  const [, y, month, day] = m.map(Number);
  const d = new Date(0);
  d.setFullYear(y, month - 1, day);
  d.setHours(0, 0, 0, 0);
  return d.getFullYear() === y && d.getMonth() === month - 1 && d.getDate() === day ? d : null;
}

function tile(p) {
  const people = new Set(Array.isArray(p.people) ? p.people.filter(Boolean) : []).size;
  const a = el('a', {
    class: 'tile gp-tile', href: '#/photo/' + encodeURIComponent(p.id),
    'aria-label': `Open photo: ${p.alt || 'Photo'}${people ? `, ${people} recognised people` : ''}`,
    style: { backgroundColor: p.color || 'var(--raised)' },
  }, el('img', {
    src: imgUrl(p.thumb), width: Number(p.tw) > 0 ? p.tw : 300,
    height: Number(p.th) > 0 ? p.th : 300 / ratio(p),
    alt: p.alt || '', loading: 'lazy', decoding: 'async',
  }));
  if (people) a.append(el('span', { class: 'gp-people-count', 'aria-hidden': 'true', text: `${people} ${people === 1 ? 'person' : 'people'}` }));
  return a;
}

function groupsFor(entries) {
  const months = new Map();
  for (const e of entries) {
    const key = e.date ? `${e.date.getFullYear()}-${e.date.getMonth()}` : 'none';
    if (!months.has(key)) months.set(key, []);
    months.get(key).push(e);
  }
  return [...months.values()];
}

function runsFor(entries) {
  const counts = new Map();
  for (const e of entries) if (e.date) counts.set(+e.date, (counts.get(+e.date) || 0) + 1);
  const runs = [];
  for (const e of entries) {
    const day = e.date && counts.get(+e.date) >= 8 ? +e.date : null;
    if (!runs.length || runs[runs.length - 1].day !== day) runs.push({ day, entries: [] });
    runs[runs.length - 1].entries.push(e);
  }
  return runs;
}

function sizeRun(run, width, target) {
  const gap = 4;
  let row = [], sum = 0, top = 0;
  function finish(last) {
    if (!row.length) return;
    const fit = (width - gap * (row.length - 1)) / sum;
    const height = last && fit > target * 1.5 ? target : fit;
    let left = 0;
    for (const e of row) {
      const w = e.ratio * height;
      Object.assign(e.tile.style, { width: `${w}px`, height: `${height}px`, left: `${left}px`, top: `${top}px` });
      left += w + gap;
    }
    top += height + gap;
    row = []; sum = 0;
  }
  for (const e of run.entries) {
    if (row.length && (sum + e.ratio) * target + gap * row.length > width) finish(false);
    row.push(e); sum += e.ratio;
  }
  finish(true);
  run.host.style.height = `${Math.max(0, top - gap)}px`;
}

export function renderGrid(container, photos, opts = {}) {
  container.classList.add('grid', 'gp-grid');
  const entries = photos.map((p) => ({ photo: p, date: photoDate(p.date), ratio: ratio(p), tile: tile(p) }));
  entries.sort((a, b) => (b.date ? +b.date : -Infinity) - (a.date ? +a.date : -Infinity));
  const runs = [];
  const sections = groupsFor(entries).map((month) => {
    const title = month[0].date?.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }) || 'No date';
    const section = el('section', { class: 'gp-month', 'aria-label': title }, el('h2', { class: 'gp-month-title', text: title }));
    for (const run of runsFor(month)) {
      if (run.day != null) section.append(el('h3', { class: 'gp-day-title', text: new Date(run.day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) }));
      run.host = el('div', { class: 'gp-photo-run' }, run.entries.map((e) => e.tile));
      section.append(run.host); runs.push(run);
    }
    return section;
  });
  container.replaceChildren(...sections);
  const tiles = entries.map((e) => e.tile);
  function onKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    const i = tiles.indexOf(event.target.closest('.gp-tile'));
    if (i < 0) return;
    event.preventDefault();
    tiles[Math.max(0, Math.min(tiles.length - 1, i + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1)))]?.focus();
  }
  container.addEventListener('keydown', onKey);
  let width = 0;
  function layout() {
    const next = container.clientWidth;
    if (!next) return;
    width = next;
    const target = Number(opts.height) > 0 ? Number(opts.height) : (width < 600 ? 140 : 220);
    for (const run of runs) sizeRun(run, width, target);
  }
  const resize = debounce(() => { if (container.clientWidth !== width) layout(); }, 80);
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  layout();
  return {
    photos: entries.map((e) => e.photo), relayout: layout,
    destroy() { ro.disconnect(); resize.cancel(); container.removeEventListener('keydown', onKey); },
  };
}
