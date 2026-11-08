import { el, avatar, plural } from './util.js';

export function peopleStrip(store) {
  const people = store.people.filter((p) => p.photos > 0)
    .sort((a, b) => b.photos - a.photos || a.name.localeCompare(b.name)).slice(0, 14);
  if (people.length < 3) return document.createDocumentFragment();
  const row = el('ul', { class: 'plain gp-people-strip', tabindex: '0', 'aria-label': 'People with the most photos' },
    people.map((p) => {
      const picture = avatar(p, 'gp-strip-avatar');
      picture.setAttribute('width', '72');
      picture.setAttribute('height', '72');
      return el('li', {}, el('a', {
        class: 'gp-strip-person', href: '#/person/' + encodeURIComponent(p.id),
        'aria-label': `${p.name}, ${plural(p.photos, 'photo')}`,
      }, picture, el('span', { text: p.name.trim() })));
    }));
  return el('section', { class: 'gp-people-preview', 'aria-label': 'People' },
    el('div', { class: 'gp-people-heading' }, el('h2', { text: 'People' }),
      el('a', { href: '#/people', text: 'See all', 'aria-label': 'See all people' })),
    el('div', { class: 'gp-people-scroll' }, row));
}
