import { el, label, plural, fmtDate, imgUrl } from '../util.js';
import { link } from '../filters.js';

export function mount(root, ctx) {
  const { store } = ctx;
  let newest = true;

  const places = el('ul', { class: 'plain places' }, store.scenes.map((s) => el('li', {},
    el('a', { class: 'place', href: link({ scene: [s.id] }) },
      el('img', { src: imgUrl(s.first.thumb), alt: '', width: s.first.tw, height: s.first.th, loading: 'lazy', decoding: 'async', style: { backgroundColor: s.first.color } }),
      el('span', { class: 'place-cap' }, el('span', { class: 'place-name', text: label(s.id) }), el('span', { class: 'muted num', text: plural(s.count, 'photo') }))))));

  const byDate = new Map();
  for (const p of store.photos) {
    const d = p.date || 'none';
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(p);
  }
  const timeline = el('div');
  const segBtns = [['Newest first', true], ['Oldest first', false]].map(([text, v]) =>
    el('button', { type: 'button', 'aria-pressed': 'false', onclick: () => { newest = v; render(); } }, text));

  function render() {
    segBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(i === 0 === newest)));
    const dates = [...byDate.keys()].filter((d) => d !== 'none').sort();
    if (newest) dates.reverse();
    if (byDate.has('none')) dates.push('none');
    timeline.replaceChildren(...dates.map((d) => {
      const list = byDate.get(d);
      return el('a', { class: 'tl-row', href: link({ date: [d] }) },
        el('span', { class: 'tl-date' }, el('b', { text: d === 'none' ? 'No date' : fmtDate(d) }), el('span', { class: 'muted small num', text: plural(list.length, 'photo') })),
        el('span', { class: 'tl-strip', 'aria-hidden': 'true' }, list.slice(0, 8).map((p) =>
          el('img', { src: imgUrl(p.thumb), alt: '', width: p.tw, height: p.th, loading: 'lazy', decoding: 'async', style: { backgroundColor: p.color } }))));
    }));
  }

  root.append(
    el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Places and dates' }), el('p', { text: 'Browse the archive by where a photo was taken or the day it was taken.' }))),
    el('h2', { text: 'Places' }), places,
    el('div', { class: 'tl-tools' }, el('h2', { text: 'Timeline' }), el('div', { class: 'seg', role: 'group', 'aria-label': 'Sort order' }, segBtns)),
    timeline);
  render();
  return { title: 'Places and dates' };
}
