import { el, plural, avatar } from '../util.js';

export function personRow(p) {
  const meta = p.section || p.role || '';
  return el('li', {},
    el('a', { class: 'row', href: '#/person/' + encodeURIComponent(p.id) },
      avatar(p),
      el('span', { class: 'row-main' }, el('span', { class: 'row-name', text: p.name }), meta && el('span', { class: 'muted small', text: meta })),
      el('span', { class: 'row-n muted small num', text: plural(p.photos || 0, 'photo') })));
}

export function mount(root, ctx) {
  const { store } = ctx;
  let kind = 'student', q = '';
  const kinds = [['student', 'Students'], ['staff', 'Staff']];
  const segBtns = kinds.map(([k, text]) => el('button', { type: 'button', 'aria-pressed': 'false', onclick: () => { kind = k; render(); } },
    `${text} ${store.people.filter((p) => p.kind === k).length}`));
  const search = el('input', { type: 'search', class: 'input', placeholder: 'Search by name', 'aria-label': 'Search people' });
  const found = el('ul', { class: 'plain rows' });
  const none = el('details', { class: 'disclosure' });
  const msg = el('p', { class: 'empty muted', hidden: true });
  search.addEventListener('input', () => { q = search.value.trim().toLowerCase(); render(); });

  function render() {
    segBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(kinds[i][0] === kind)));
    const list = store.people.filter((p) => p.kind === kind && p.name.toLowerCase().includes(q)).sort((a, b) => a.name.localeCompare(b.name));
    const yes = list.filter((p) => p.photos > 0), no = list.filter((p) => !(p.photos > 0));
    found.replaceChildren(...yes.map(personRow));
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
