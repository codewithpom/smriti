import { el, label, plural, debounce, fmtDate } from '../util.js';
import { fromQuery, hashFor, isActive, makePredicate, empty, KEYS } from '../filters.js';
import { renderGrid } from '../grid.js';

const TAGS_SHOWN = 12;

export function mount(root, ctx) {
  const { store } = ctx;
  let state = fromQuery(ctx.query);
  let grid = null, showAllTags = false, filtersOpen = false;
  const toggles = []; // {b, group, value}

  const count = el('p', { class: 'muted num', 'aria-live': 'polite' });
  const host = el('div');
  const search = el('input', { type: 'search', class: 'input', placeholder: 'Search people, places, tags, text', 'aria-label': 'Search photos', value: state.q });
  const onSearch = debounce(() => { state.q = search.value; commit(true); }, 200);
  search.addEventListener('input', onSearch);
  const clearBtn = el('button', { type: 'button', class: 'btn btn-quiet', onclick: clearAll }, 'Clear all');
  const badge = el('span', { class: 'pill', hidden: true });
  const groups = el('div', { class: 'fgroups', id: 'fgroups' });
  const toggleBtn = el('button', {
    type: 'button', class: 'btn filters-toggle', 'aria-expanded': 'false', 'aria-controls': 'fgroups',
    onclick: () => { filtersOpen = !filtersOpen; groups.classList.toggle('open', filtersOpen); toggleBtn.setAttribute('aria-expanded', String(filtersOpen)); },
  }, 'Filters', badge);

  function toggle(group, value) {
    const a = state[group], i = a.indexOf(value);
    if (i < 0) a.push(value); else a.splice(i, 1);
    commit();
  }
  function chip(group, value, text, n) {
    const b = el('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', onclick: () => toggle(group, value) },
      text, n != null && el('span', { class: 'chip-n muted num', text: n }));
    toggles.push({ b, group, value });
    return b;
  }
  const group = (name, ...kids) => el('div', { class: 'fgroup', role: 'group', 'aria-label': name }, el('span', { class: 'fgroup-label', text: name }), ...kids);

  // Scene
  groups.append(group('Scene', store.scenes.map((s) => chip('scene', s.id, label(s.id), s.count))));

  // People: combobox via datalist, selected people shown as removable chips
  const named = store.people.filter((p) => p.photos > 0).sort((a, b) => a.name.localeCompare(b.name));
  const nameToId = new Map();
  const opts = named.map((p) => {
    const v = nameToId.has(p.name) ? `${p.name} (${p.id})` : p.name;
    nameToId.set(v, p.id);
    return el('option', { value: v, label: plural(p.photos, 'photo') });
  });
  const sel = el('span', { class: 'chips' });
  const addPerson = () => {
    const id = nameToId.get(search2.value.trim());
    if (!id) return;
    if (!state.person.includes(id)) state.person.push(id);
    search2.value = '';
    commit();
  };
  const search2 = el('input', { class: 'input', list: 'people-options', placeholder: 'Add a person', 'aria-label': 'Add a person', autocomplete: 'off', onchange: addPerson, oninput: addPerson });
  const allBox = el('input', { type: 'checkbox', onchange: () => { state.all = allBox.checked; commit(); } });
  const allLabel = el('label', { class: 'check' }, allBox, 'Only photos with all of them');
  groups.append(group('People', sel, search2, el('datalist', { id: 'people-options' }, opts), allLabel));

  // Tags
  const tagBtns = store.tags.map((t) => chip('tag', t.t, label(t.t), t.n));
  const more = el('button', { type: 'button', class: 'btn btn-quiet', onclick: () => { showAllTags = !showAllTags; sync(); } });
  groups.append(group('Tags', tagBtns, store.tags.length > TAGS_SHOWN && more));

  // Year / day
  const years = new Map(), days = [];
  for (const [d, n] of store.dates) {
    if (d === 'none') continue;
    days.push([d, n]);
    years.set(d.slice(0, 4), (years.get(d.slice(0, 4)) || 0) + n);
  }
  days.sort((a, b) => a[0].localeCompare(b[0]));
  const dateSel = el('select', { class: 'input', 'aria-label': 'Year or day', onchange: () => { state.date = dateSel.value ? [dateSel.value] : []; commit(); } },
    el('option', { value: '', text: 'Any date' }),
    el('optgroup', { label: 'Year' }, [...years].sort().map(([y, n]) => el('option', { value: y, text: `${y} (${n})` }))),
    el('optgroup', { label: 'Day' }, days.map(([d, n]) => el('option', { value: d, text: `${fmtDate(d)} (${n})` }))),
    store.dates.has('none') && el('option', { value: 'none', text: `No date (${store.dates.get('none')})` }));
  groups.append(group('Year or day', dateSel));

  function sync() {
    for (const { b, group: g, value } of toggles) b.setAttribute('aria-pressed', String(state[g].includes(value)));
    tagBtns.forEach((b, i) => { b.hidden = !showAllTags && i >= TAGS_SHOWN && b.getAttribute('aria-pressed') !== 'true'; });
    more.textContent = showAllTags ? 'Fewer' : 'More';
    sel.replaceChildren(...state.person.map((id) => {
      const p = store.peopleById.get(id);
      const name = p ? p.name : id;
      return el('button', { type: 'button', class: 'chip', 'aria-pressed': 'true', 'aria-label': `Remove ${name}`, onclick: () => { toggle('person', id); search2.focus(); } }, name, ' ×');
    }));
    allLabel.hidden = state.person.length < 2;
    allBox.checked = state.all;
    dateSel.value = state.date[0] || '';
    const n = KEYS.reduce((s, k) => s + state[k].length, 0);
    badge.hidden = n === 0;
    badge.textContent = n;
    clearBtn.hidden = !isActive(state);
  }

  function results() {
    const filtered = store.photos.filter(makePredicate(state, store));
    ctx.setList(filtered);
    count.textContent = isActive(state) ? `${filtered.length} of ${plural(store.photos.length, 'photo')}` : plural(filtered.length, 'photo');
    grid?.destroy();
    grid = null;
    if (!filtered.length) {
      host.replaceChildren(el('p', { class: 'empty muted', text: 'No photos match these filters.' }));
      return;
    }
    const g = el('div');
    host.replaceChildren(g);
    grid = renderGrid(g, filtered);
  }

  function commit(replace = false) { ctx.setHash(hashFor(state), replace); sync(); results(); }
  function clearAll() { state = empty(); search.value = ''; commit(); search.focus(); }

  root.append(
    el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Gallery' }), count)),
    el('div', { class: 'toolbar' },
      el('div', { class: 'toolbar-top' }, el('div', { class: 'search' }, search), toggleBtn, clearBtn),
      groups),
    host);
  sync();
  results();

  return {
    title: 'Gallery',
    update(q) { state = fromQuery(q); search.value = state.q; sync(); results(); },
    unmount() { onSearch.cancel(); grid?.destroy(); },
  };
}
