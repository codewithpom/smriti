function filterPeople(people, text) {
  const query = text.trim().toLowerCase();
  const result = people.filter(p => [p.name, p.id, p.reg].some(v => String(v || '').toLowerCase().includes(query))).slice(0, 8);
  // "10123" or "S10123", optionally followed by a name: "10123 Rahul Das"
  const m = /^S?(\d{4,6})(?:\s+(.{1,60}))?$/i.exec(text.trim());
  if (m) {
    const id = 'S' + m[1], nm = (m[2] || '').replace(/\s+/g, ' ').trim();
    if (!people.some(p => p.id.toLowerCase() === id.toLowerCase())) result.push({id, name: `Use ${id} (new person)` + (nm ? ': ' + nm : ''), newPerson: true, newName: nm});
  }
  // "staff Rahul Das" adds staff; "student Rahul Das" or "student MB27 Rahul Das" adds a student with no reg number yet
  const tempId = (prefix, nm) => {
    let h = 0; for (const ch of nm.toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) % 46656;
    return prefix + (nm.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 26) || 'x') + '_' + h.toString(36);
  };
  const sm = /^staff[:\s]+(.{2,60})$/i.exec(text.trim());
  if (sm) {
    const nm = sm[1].replace(/\s+/g, ' ').trim();
    result.push({id: tempId('STAFF_N_', nm), name: 'Add new staff: ' + nm, newPerson: true, newName: nm, kind: 'staff'});
  }
  const um = /^(?:student|stu)[:\s]+(?:(MB\d{2})\s+)?(.{2,60})$/i.exec(text.trim());
  if (um) {
    const nm = um[2].replace(/\s+/g, ' ').trim(), batch = (um[1] || 'MB28').toUpperCase();
    result.push({id: tempId('STU_N_', nm), name: `Add new student, no reg number yet (${batch}): ${nm}`, newPerson: true, newName: nm, kind: 'student', batch});
  }
  return result;
}
if (typeof module !== 'undefined') module.exports = {filterPeople};
if (typeof window !== 'undefined') window.SheetLogic = {filterPeople};
if (typeof document !== 'undefined') (() => {
  'use strict';
  const cfg = window.SHEET, storage = 'cluster-names-' + cfg.fingerprint;
  const people = new Map(cfg.people.map(p => [p.id, p]));
  const cards = [...document.querySelectorAll('[data-cluster]')];
  const answers = Object.create(null);
  const newPeople = Object.create(null);   // names typed for people who are not in the list yet
  const newBatch = Object.create(null);    // batch typed for new students without a reg number
  let active = null;
  const searches = new WeakMap();
  try {
    const saved = JSON.parse(localStorage.getItem(storage) || '{}');
    for (const c of cfg.clusters) {
      const v = saved?.[c];
      if (typeof v === 'string' && (v === 'none' || v === 'skip' || people.has(v) || /^(S\d{4,6}|STAFF_N_[a-z0-9_]{1,40}|STU_N_[a-z0-9_]{1,40})$/.test(v))) answers[c] = v;
    }
    const nb = JSON.parse(localStorage.getItem(storage + '-batch') || '{}');
    for (const [id, b] of Object.entries(nb)) if (/^MB\d{2}$/.test(b)) newBatch[id] = b;
    const np = JSON.parse(localStorage.getItem(storage + '-people') || '{}');
    for (const [id, nm] of Object.entries(np)) if (/^(S\d{4,6}|STAFF_N_[a-z0-9_]{1,40}|STU_N_[a-z0-9_]{1,40})$/.test(id) && typeof nm === 'string') newPeople[id] = nm.slice(0, 60);
  } catch (_) { /* Storage may be disabled for local files. */ }
  function portrait(p) {
    if (p.avatar) {
      const img = document.createElement('img');
      img.src = 'people/' + encodeURIComponent(p.id) + '.jpg'; img.alt = ''; return img;
    }
    const span = document.createElement('span'); span.className = 'initials';
    span.setAttribute('aria-hidden', 'true');
    span.textContent = p.name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('') || '?';
    return span;
  }
  function close(card) {
    card.querySelector('[role=listbox]').hidden = true;
    const input = card.querySelector('input');
    input.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-activedescendant', '');
    searches.delete(card);
  }
  function activate(card, focus = true) {
    if (!card) return;
    if (active && active !== card) close(active);
    active = card;
    for (const c of cards) {
      c.classList.toggle('active', c === card);
      if (c === card) c.setAttribute('aria-current', 'true'); else c.removeAttribute('aria-current');
    }
    if (focus) {
      card.focus({preventScroll: true});
      card.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
  }
  function update() {
    let n = 0, faces = 0;
    for (const card of cards) {
      const value = answers[card.dataset.cluster], label = card.querySelector('.answer');
      card.classList.toggle('answered', Boolean(value)); label.replaceChildren();
      if (value) {
        n++; faces += cfg.sizes[card.dataset.cluster];
        const p = people.get(value);
        if (p) label.append(portrait(p));
        const staffNew = !p && /^STAFF_N_/.test(value);
        const fresh = staffNew ? (newPeople[value] || value) + ' (new staff)'
          : !p && /^STU_N_/.test(value) ? (newPeople[value] || value) + ' (new student, no reg number)'
          : !p && /^S\d{4,6}$/.test(value) ? (newPeople[value] ? newPeople[value] + ' (' + value + ', new)' : value + ' (new person)') : '';
        label.append(document.createTextNode('✓ ' + (p ? p.name || p.id : fresh || (value === 'none' ? 'Not a person' : value === 'skip' ? 'Mixed, skip' : value))));
      }
    }
    document.querySelector('#progress').textContent = `naming the first ${cfg.clusters.length} clusters covers ${Object.values(cfg.sizes).reduce((a,b) => a+b, 0)} of ${cfg.total} faces — answered ${n} clusters, ${faces} faces`;
    try { localStorage.setItem(storage, JSON.stringify(answers)); localStorage.setItem(storage + '-people', JSON.stringify(newPeople)); localStorage.setItem(storage + '-batch', JSON.stringify(newBatch)); } catch (_) {}
  }
  function pick(card, row) {
    if (row.newPerson && row.newName) newPeople[row.id] = row.newName;
    if (row.newPerson && row.batch) newBatch[row.id] = row.batch;
    answer(card, row.id);
  }
  function startNew(card) {
    const input = card.querySelector('input');
    input.focus(); input.placeholder = 'New student: 10123 Rahul Das, or student Rahul Das if no reg number. New staff: staff Rahul Das. Then Enter'; render(card);
  }
  function answer(card, value) {
    const cid = card.dataset.cluster;
    if (value) answers[cid] = value; else delete answers[cid];
    card.querySelector('input').value = ''; close(card); update();
    if (value) {
      const start = cards.indexOf(card);
      const next = cards.slice(start + 1).concat(cards.slice(0, start)).find(c => !answers[c.dataset.cluster]);
      activate(next || card);
    } else activate(card);
  }
  function render(card, index = -1) {
    const input = card.querySelector('input'), list = card.querySelector('[role=listbox]');
    const results = filterPeople(cfg.people, input.value);
    searches.set(card, {results, index}); list.replaceChildren();
    results.forEach((p, i) => {
      const row = document.createElement('div'); row.role = 'option'; row.id = list.id + '-' + i;
      row.dataset.option = i; row.setAttribute('aria-selected', String(i === index));
      row.append(portrait(p));
      const text = document.createElement('span'), name = document.createElement('span'), detail = document.createElement('small');
      name.textContent = p.name || p.id;
      detail.textContent = p.newPerson ? 'New person' : p.kind === 'student' ? 'student ' + (p.batch || '') : 'staff';
      text.append(name, detail); row.append(text); list.append(row);
    });
    list.hidden = !results.length; input.setAttribute('aria-expanded', String(Boolean(results.length)));
    input.setAttribute('aria-activedescendant', index >= 0 ? list.id + '-' + index : '');
    if (index >= 0) list.children[index].scrollIntoView({block: 'nearest'});
  }
  document.addEventListener('focusin', e => {
    const card = e.target.closest('[data-cluster]');
    if (card) activate(card, false); else if (active) close(active);
  });
  document.addEventListener('input', e => {
    const card = e.target.closest('[data-cluster]');
    if (card && e.target.matches('input')) render(card);
  });
  document.addEventListener('click', e => {
    const card = e.target.closest('[data-cluster]');
    if (!card) { if (active) close(active); return; }
    activate(card, false);
    const option = e.target.closest('[data-option]'), button = e.target.closest('button');
    if (option) pick(card, searches.get(card).results[Number(option.dataset.option)]);
    else if (button && button.dataset.action === 'new') startNew(card);
    else if (button) answer(card, button.dataset.person || button.dataset.value);
    else if (e.target.matches('input')) render(card);
  });
  document.addEventListener('keydown', e => {
    if (e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
    const field = e.target.matches('input,textarea,select,[contenteditable=true]');
    const card = e.target.closest('[data-cluster]') || active;
    if (field) {
      if (!card || !e.target.matches('[role=combobox]')) return;
      let state = searches.get(card);
      if (e.key === 'Escape') { e.preventDefault(); close(card); activate(card); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (!state) { render(card); state = searches.get(card); }
        if (state.results.length) render(card, (state.index + (e.key === 'ArrowDown' ? 1 : state.index < 0 ? 0 : -1) + state.results.length) % state.results.length);
      } else if (e.key === 'Enter') {
        e.preventDefault(); if (state && state.index >= 0) pick(card, state.results[state.index]);
        else if (state && state.results.length === 1 && state.results[0].newPerson) pick(card, state.results[0]);
      }
      return;
    }
    if (e.key === '?') { e.preventDefault(); const help = document.querySelector('#help'); help.hidden = !help.hidden; return; }
    if (!card) return;
    // Preserve native activation for focused buttons.
    if (e.target.closest('button') && e.key === 'Enter') return;
    const key = e.key;
    if (['j','k','ArrowDown','ArrowUp'].includes(key)) {
      e.preventDefault(); const delta = key === 'j' || key === 'ArrowDown' ? 1 : -1;
      activate(cards[Math.max(0, Math.min(cards.length - 1, cards.indexOf(card) + delta))]);
    } else if (['1','2','3'].includes(key)) {
      e.preventDefault(); const button = card.querySelector(`[data-key="${key}"]`); if (button) answer(card, button.dataset.person);
    } else if (key === 'a') {
      e.preventDefault(); startNew(card);
    } else if (['n','s','x','Backspace','Delete'].includes(key)) {
      e.preventDefault(); answer(card, key === 'n' ? 'none' : key === 's' ? 'skip' : undefined);
    } else if (key === '/' || key === 'Enter') {
      e.preventDefault(); card.querySelector('input').focus(); render(card);
    }
  });
  document.querySelector('#download').addEventListener('click', () => {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([JSON.stringify({fingerprint: cfg.fingerprint, clusters: cfg.clusters, answers,
      people: Object.fromEntries(Object.entries(newPeople).filter(([id, nm]) => nm && Object.values(answers).includes(id)).map(([id, nm]) => [id, id.startsWith('STAFF_N_') ? {name: nm, kind: 'staff'} : id.startsWith('STU_N_') ? {name: nm, kind: 'student', batch: newBatch[id] || 'MB28'} : {name: nm}]))})], {type: 'application/json'}));
    a.href = url; a.download = 'cluster_names.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  update(); activate(cards.find(c => !answers[c.dataset.cluster]) || cards[0]);
})();
