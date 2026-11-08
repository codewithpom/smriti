import { el, plural, avatar, label } from '../util.js';
import { link } from '../filters.js';
import { renderGrid } from '../grid.js';

const TIER = { close: 'pill-positive', classmates: '', 'seen together': 'pill-outline' };

export function mount(root, ctx) {
  const { store } = ctx;
  const p = store.peopleById.get(ctx.parts[1]);
  if (!p) {
    root.append(
      el('div', { class: 'page-head' }, el('h1', { text: 'Person not found' })),
      el('p', {}, el('a', { class: 'btn', href: '#/people', text: 'Back to people' })));
    return { title: 'Person not found' };
  }
  const photos = store.photos.filter((x) => x.people.includes(p.id));
  store.list = photos;
  const meta = [p.kind === 'staff' ? 'Staff' : 'Student', p.batch, p.section || p.role, plural(photos.length, 'photo'),
    p.community != null && `Friend group ${Number(p.community) + 1}`].filter(Boolean).join(' · ');

  const portrait = avatar(p, 'avatar-lg gp-person-avatar');
  portrait.setAttribute('width', '120');
  portrait.setAttribute('height', '120');
  root.append(el('header', { class: 'person-head gp-person-head' }, portrait,
    el('div', { class: 'gp-person-heading' }, el('h1', { text: p.name }), el('p', { class: 'muted', text: meta }))));

  const friends = (p.friends || []).filter((f) => store.peopleById.has(f.id)).sort((a, b) => b.weight - a.weight).slice(0, 10);
  if (friends.length) {
    root.append(el('h2', { class: 'section-title', text: 'Closest friends' }),
      el('ul', { class: 'plain rows' }, friends.map((f) => {
        const o = store.peopleById.get(f.id);
        return el('li', { class: 'friend-row' },
          el('a', { class: 'row', href: '#/person/' + encodeURIComponent(o.id) },
            avatar(o), el('span', { class: 'row-main' }, el('span', { class: 'row-name', text: o.name }))),
          el('span', { class: 'pill ' + (TIER[f.tier] || ''), text: label(f.tier || '') }),
          el('a', { class: 'together small num', href: link({ person: [p.id, o.id], all: true }), text: `${plural(f.shared || 0, 'photo')} together` }));
      })));
  }

  root.append(el('h2', { class: 'section-title', text: 'Photos' }));
  if (!photos.length) {
    root.append(el('p', { class: 'muted', text: 'Not found in any photo yet.' }));
    return { title: p.name };
  }
  const host = el('div');
  root.append(host);
  const grid = renderGrid(host, photos);
  return { title: p.name, unmount: () => grid.destroy() };
}
