// Justified rows (Google Photos style). Last row keeps the target height.
import { el, imgUrl } from './util.js';

const ar = (p) => (p.tw && p.th ? p.tw / p.th : p.w / p.h) || 1;

function tile(p) {
  return el('a', { class: 'tile', href: '#/photo/' + encodeURIComponent(p.id), style: { backgroundColor: p.color } },
    el('img', { src: imgUrl(p.thumb), width: p.tw, height: p.th, alt: p.alt || '', loading: 'lazy', decoding: 'async' }));
}

export function renderGrid(container, photos, opts = {}) {
  container.classList.add('grid');
  const gap = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--row-gap')) || 6;
  const tiles = photos.map(tile);
  let width = 0;

  function layout() {
    const W = container.clientWidth;
    if (!W) return;
    width = W;
    const H = opts.height || (window.innerWidth <= 860 ? 140 : 220);
    const rows = [];
    let row = [], sum = 0;
    photos.forEach((p, i) => {
      row.push(i); sum += ar(p);
      if (sum * H + gap * (row.length - 1) >= W) { rows.push({ row, sum, full: true }); row = []; sum = 0; }
    });
    if (row.length) rows.push({ row, sum, full: false });
    container.replaceChildren(...rows.map(({ row, sum, full }) => {
      const h = full ? (W - gap * (row.length - 1)) / sum : H;
      const r = el('div', { class: 'grid-row', style: { height: h + 'px' } });
      for (const i of row) {
        tiles[i].style.width = ar(photos[i]) * h + 'px';
        tiles[i].style.height = h + 'px';
        r.append(tiles[i]);
      }
      return r;
    }));
  }

  layout();
  const ro = new ResizeObserver(() => { if (Math.abs(container.clientWidth - width) > 1) layout(); });
  ro.observe(container);
  return { relayout: layout, destroy: () => ro.disconnect() };
}
