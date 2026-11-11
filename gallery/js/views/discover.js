// Home feed. Every visit draws a new seed, so the shelves show different photos each time.
// "All photos" (#/all) keeps the dated, filterable grid.
import { el, imgUrl, label, plural, fmtDate } from '../util.js';
import { link } from '../filters.js';

// mulberry32: tiny seeded PRNG, so a shared #/?seed=... link would show the same shelves.
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function shuffled(list, rand) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}
const pick = (list, rand) => list[Math.floor(rand() * list.length)];

// Pure: which shelves to show for this seed. Tested without a DOM.
export function buildShelves(store, seed, today = new Date()) {
  const rand = rng(seed);
  const photos = store.photos;
  const shelves = [];
  const used = new Set();
  const take = (list, n) => {
    const fresh = list.filter((p) => !used.has(p.id)).slice(0, n);
    fresh.forEach((p) => used.add(p.id));
    return fresh;
  };

  shelves.push({ id: 'fresh', title: 'Fresh picks', sub: 'A new mix every visit', photos: take(shuffled(photos, rand), 20) });

  const md = `-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const onDay = photos.filter((p) => p.date && p.date.slice(4, 10) === md && p.date.slice(0, 4) !== String(today.getFullYear()));
  if (onDay.length) shelves.push({ id: 'today', title: 'On this day', sub: 'Photos from this date in earlier years', photos: take(shuffled(onDay, rand), 16) });

  const people = store.people.filter((p) => p.photos >= 6 && p.name);
  if (people.length) {
    const who = pick(people, rand);
    shelves.push({ id: 'person', title: `Spotlight: ${who.name}`, sub: plural(who.photos, 'photo'), href: '#/person/' + encodeURIComponent(who.id),
      photos: take(shuffled(photos.filter((p) => p.people.includes(who.id)), rand), 14) });
  }

  const scenes = store.scenes.filter((s) => s.count >= 6);
  if (scenes.length) {
    const s = pick(scenes, rand);
    shelves.push({ id: 'scene', title: label(s.id), sub: plural(s.count, 'photo'), href: link({ scene: [s.id] }),
      photos: take(shuffled(photos.filter((p) => p.scene === s.id), rand), 14) });
  }

  const days = [...store.dates].filter(([d, n]) => d !== 'none' && n >= 6);
  if (days.length) {
    const [d, n] = pick(days, rand);
    shelves.push({ id: 'day', title: fmtDate(d), sub: `A random day, ${plural(n, 'photo')}`, href: link({ date: [d] }),
      photos: take(shuffled(photos.filter((p) => p.date && p.date.startsWith(d)), rand), 14) });
  }

  const recent = photos.filter((p) => p.date).sort((a, b) => b.date.localeCompare(a.date));
  shelves.push({ id: 'recent', title: 'Most recent', sub: 'Latest dated photos', photos: recent.slice(0, 14) });
  return shelves.filter((s) => s.photos.length);
}

function card(p, i) {
  return el('a', { class: 'dv-card', href: '#/photo/' + encodeURIComponent(p.id), style: { backgroundColor: p.color || 'var(--raised)', '--i': i },
    'aria-label': `Open photo: ${p.alt || 'Photo'}` },
  el('img', { src: imgUrl(p.thumb), alt: p.alt || '', loading: 'lazy', decoding: 'async', width: p.tw || 300, height: p.th || 300 }));
}

export function mount(root, ctx) {
  const { store } = ctx;
  let seed = Number(ctx.query.get('seed')) || (Math.random() * 2 ** 32) >>> 0;
  const host = el('div', { class: 'dv' });

  function render() {
    const shelves = buildShelves(store, seed);
    ctx.setList([...new Map(shelves.flatMap((s) => s.photos).map((p) => [p.id, p])).values()]); // lightbox arrows walk the feed
    host.replaceChildren(...shelves.map((s, n) => el('section', { class: 'dv-shelf', 'aria-label': s.title, style: { '--n': n } },
      el('div', { class: 'dv-head' },
        el('div', {}, el('h2', { text: s.title }), el('p', { class: 'muted small', text: s.sub })),
        s.href && el('a', { href: s.href, class: 'dv-more', text: 'See all' })),
      el('div', { class: 'dv-scroll', tabindex: '0', role: 'group', 'aria-label': `${s.title} photos` }, s.photos.map(card)))));
  }

  const shuffle = el('button', { type: 'button', class: 'btn', onclick: () => { seed = (Math.random() * 2 ** 32) >>> 0; render(); host.firstChild?.scrollIntoView?.({ block: 'nearest' }); } }, 'Shuffle');
  const asks = store.questions.length
    ? el('a', { class: 'dv-ask', href: '#/review' }, el('b', {}, `Help name ${plural(store.questions.length, 'open question')}`), el('span', { class: 'muted small' }, 'Your answers teach the face matcher.'))
    : null;
  root.append(
    el('div', { class: 'page-head dv-top' },
      el('div', {}, el('h1', { text: 'Home' }), el('p', { class: 'muted num', text: `${plural(store.photos.length, 'photo')} in the archive` })),
      el('div', { class: 'dv-actions' }, shuffle, el('a', { class: 'btn btn-primary', href: '#/all' }, 'All photos'))),
    asks, host);
  render();
  return { title: 'Home' };
}
