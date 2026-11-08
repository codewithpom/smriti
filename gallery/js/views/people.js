import { el, plural, avatar } from '../util.js';

export function personRow(p) {
  const meta = p.section || p.role || '';
  return el('li', {},
    el('a', { class: 'row', href: '#/person/' + encodeURIComponent(p.id) },
      avatar(p),
      el('span', { class: 'row-main' }, el('span', { class: 'row-name', text: p.name }), meta && el('span', { class: 'muted small', text: meta })),
      el('span', { class: 'row-n muted small num', text: plural(p.photos || 0, 'photo') })));
}

function personCard(p) {
  const picture = avatar(p, 'gp-card-avatar');
  picture.setAttribute('width', '96');
  picture.setAttribute('height', '96');
  return el('li', {}, el('a', {
    class: 'gp-person-card', href: '#/person/' + encodeURIComponent(p.id),
    'aria-label': `${p.name}, ${plural(p.photos, 'photo')}`,
  }, picture, el('span', { class: 'gp-person-name', text: p.name }),
  el('span', { class: 'muted small num', text: plural(p.photos, 'photo') })));
}

export function mount(root, ctx) {
  const { store } = ctx;
  const batches = [...new Set(store.people.filter((p) => p.kind === 'student' && p.batch).map((p) => p.batch))].sort();
  const kinds = batches.length ? batches.map((batch) => [batch, batch]) : [['student', 'Students']];
  if (batches.length && store.people.some((p) => p.kind === 'student' && !p.batch)) kinds.push(['student', 'Students']);
  kinds.push(['staff', 'Staff']);
  let kind = kinds[0][0], q = '';
  const matches = (p, k) => k === 'staff' ? p.kind === 'staff' : k === 'student' ? p.kind === 'student' && (!batches.length || !p.batch) : p.kind === 'student' && p.batch === k;
  const segBtns = kinds.map(([k, text]) => el('button', { type: 'button', 'aria-pressed': 'false', onclick: () => { kind = k; render(); } },
    `${text} ${store.people.filter((p) => matches(p, k)).length}`));
  const search = el('input', { type: 'search', class: 'input', placeholder: 'Search by name', 'aria-label': 'Search people' });
  const found = el('ul', { class: 'plain gp-people-grid', 'aria-label': 'People in photos' });
  found.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const cards = [...found.querySelectorAll('.gp-person-card')];
    const index = cards.indexOf(event.target);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    const columns = getComputedStyle(found).gridTemplateColumns.split(/\s+/).length;
    const rtl = getComputedStyle(found).direction === 'rtl' ? -1 : 1;
    const step = { ArrowLeft: -rtl, ArrowRight: rtl, ArrowUp: -columns, ArrowDown: columns }[event.key];
    event.preventDefault();
    cards[Math.max(0, Math.min(cards.length - 1, index + step))].focus();
  });
  const none = el('details', { class: 'disclosure' });
  const msg = el('p', { class: 'empty muted', hidden: true });
  search.addEventListener('input', () => { q = search.value.trim().toLowerCase(); render(); });

  function render() {
    segBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(kinds[i][0] === kind)));
    const list = store.people.filter((p) => matches(p, kind) && (p.name.toLowerCase().includes(q) || String(p.reg_no || '').includes(q))).sort((a, b) => a.name.localeCompare(b.name));
    const yes = list.filter((p) => p.photos > 0), no = list.filter((p) => !(p.photos > 0));
    yes.sort((a, b) => b.photos - a.photos || a.name.localeCompare(b.name));
    found.replaceChildren(...yes.map(personCard));
    msg.hidden = list.length > 0;
    msg.textContent = 'No one matches that search.';
    none.hidden = no.length === 0;
    none.replaceChildren(
      el('summary', {}, 'Not found in any photo yet', el('span', { class: 'muted', text: ` (${no.length})` })),
      el('ul', { class: 'plain rows' }, no.map(personRow)));
  }

  root.append(
    el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'People' }), el('p', { text: 'Everyone in the archive, with the number of photos they appear in.' }))),
    el('div', { class: 'people-tools' }, el('div', { class: 'seg', role: 'group', 'aria-label': 'Group' }, segBtns), search),
    found, msg, none);
  render();
  return { title: 'People' };
}
