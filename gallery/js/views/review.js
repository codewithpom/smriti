import { el, label, plural, ls, postJSON, SAVE_FAIL, faceCrop, avatar, imgUrl } from '../util.js';
import { store, emit, load } from '../store.js';
import { submitVote, deleteVote } from '../votes.js';
import { DEMO } from '../config.js';

const KEY = 'rkmv.answered';
const LAST = 'rkmv.lastScene';
const REASON = { disagree: 'The folder and the picture point to different places.', vague: 'It is hard to tell from the picture.', low: 'We are not sure about this one.' };
const LOCAL = location.hostname === '127.0.0.1'; // review server keeps the old /answer path; the deployed site writes votes
const saveAnswer = (q, answer, extra) => (DEMO ? Promise.resolve() : LOCAL ? postJSON('/answer', { qid: q.id, type: q.type, ...extra }) : submitVote(q, answer));
const undoSave = (q) => (DEMO ? Promise.resolve() : LOCAL ? postJSON('/undo', {}) : deleteVote(q.id));

export function mount(root, ctx) {
  const history = []; // {q, saved, counted}
  let busy = false, note = '', ui = { search: false, other: false }, answered = parseInt(ls.get(KEY, '0'), 10) || 0, lastScene = ls.get(LAST, '');

  const progress = el('p', { class: 'muted num', 'aria-live': 'polite' });
  const card = el('section', { class: 'rv-card', 'aria-label': 'Question' });
  const msg = el('p', { class: 'rv-msg small', role: 'status' });
  const hints = el('p', { class: 'muted small' });
  root.append(
    el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Help improve results' }),
      el('p', { text: 'Answer a few quick questions and the archive learns who is who and where each photo was taken.' }))),
    progress, card, msg, hints);

  const btn = (text, onclick, cls = 'btn') => el('button', { type: 'button', class: cls, onclick }, text);
  const person = (id) => store.peopleById.get(id);

  function picker(onPick) {
    const input = el('input', { type: 'search', class: 'input', placeholder: 'Find someone else', 'aria-label': 'Find someone else', autocomplete: 'off' });
    const list = el('ul', { class: 'plain pick-list' });
    const refresh = () => {
      const t = input.value.trim().toLowerCase();
      list.replaceChildren();
      if (!t) return;
      const hits = store.people.filter((p) => p.name.toLowerCase().includes(t) || String(p.reg_no || '').includes(t)).slice(0, 8);
      list.append(...(hits.length ? hits.map((p) => el('li', {},
        el('button', { type: 'button', class: 'row-btn', onclick: () => onPick(p.id) },
          avatar(p, 'avatar-sm'), el('span', { text: p.name }), el('small', { class: 'muted', text: p.section || p.role || '' }))))
        : [el('li', { class: 'muted small', text: 'No one by that name.' })]));
    };
    input.addEventListener('input', refresh);
    const picker = el('div', { class: 'picker' }, input, list);
    if (DEMO) return picker;
    const reg = el('input', { class: 'input', name: 'reg_no', required: true, inputmode: 'numeric', pattern: '[0-9]{4,6}' });
    const name = el('input', { class: 'input', name: 'name', maxlength: '60' });
    const batches = [...new Set(store.people.map((p) => p.batch).filter(Boolean))].sort();
    if (!batches.length) batches.push('MB28');
    const batch = el('select', { class: 'input', name: 'batch' }, batches.map((b) => el('option', { value: b, selected: b === (batches.includes('MB28') ? 'MB28' : batches[0]) }, b)));
    const status = el('p', { class: 'muted small', 'aria-live': 'polite', role: 'status' });
    const submit = el('button', { type: 'submit', class: 'btn btn-primary', text: 'Add and use' });
    const form = el('form', {},
      el('label', {}, 'Reg number', reg),
      el('label', {}, 'Name (optional)', name, el('span', { class: 'muted small', text: 'you can leave this empty, names come later' })),
      el('label', {}, 'Batch', batch), submit, status);
    for (const field of [reg, name, batch, submit]) field.style.minHeight = '44px';
    const question = store.questions[0];
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy || submit.disabled || !form.reportValidity()) return;
      const reg_no = reg.value.trim(), personName = name.value.trim(), selectedBatch = batch.value;
      if (!/^[0-9]{4,6}$/.test(reg_no) || personName.length > 60 || !/^MB[0-9]{2}$/.test(selectedBatch)) {
        status.textContent = 'Use 4-6 digits, a name of at most 60 characters, and a batch such as MB28.'; return;
      }
      const pid = 'S' + reg_no;
      if (store.people.some((p) => p.id === pid)) { onPick(pid); return; }
      submit.disabled = true;
      busy = true;
      status.textContent = 'Adding person...';
      try {
        let p;
        if (LOCAL) {
          const result = await postJSON('/api/addperson', { reg_no, name: personName, batch: selectedBatch });
          if (!result.ok) throw new Error(result.data?.error || 'Could not add this person. Try again.');
          p = result.data.person;
          if (!p || p.id !== pid) throw new Error('The server returned an invalid person.');
        } else {
          const { getApp } = await import('https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js');
          const { getAuth } = await import('https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js');
          const { getFirestore, doc, setDoc, serverTimestamp } = await import('https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js');
          const user = getAuth(getApp()).currentUser;
          if (!user) throw new Error('Please sign in again.');
          await setDoc(doc(getFirestore(getApp()), 'people_extra', pid), { reg_no, name: personName, batch: selectedBatch, addedBy: user.uid, ts: serverTimestamp() });
          p = { id: pid, kind: 'student', reg_no, name: personName, batch: selectedBatch, photos: 0, avatar: null, extra: true };
        }
        p = { ...p, name: p.name || 'Reg ' + reg_no, section: p.section || null };
        store.people.push(p);
        store.peopleById.set(pid, p);
        input.value = reg_no;
        refresh();
        busy = false;
        if (store.questions[0] === question) await onPick(pid);
      } catch (error) { status.textContent = error.message || 'Could not add this person. Try again.'; }
      finally { busy = false; submit.disabled = false; }
    });
    picker.append(el('details', { class: 'disclosure' }, el('summary', { style: { minHeight: '44px' } }, 'Not in the list? Add someone new'), form));
    return picker;
  }

  function candButtons(cands, pick, off = 0) {
    const list = (cands || []).slice(0, 3).map((id, i) => [person(id), i]).filter(([p]) => p);
    return list.length ? el('div', { class: 'rv-cands' }, list.map(([p, i]) =>
      el('button', { type: 'button', class: 'cand', onclick: () => pick(p.id) }, avatar(p), el('span', { text: p.name }), el('span', { class: 'hint muted small', text: String(i + 1 + off) })))) : null;
  }

  // ---- question types
  function cluster(q) {
    const crops = (q.sample || []).slice(0, 6).map((s) => { const ph = store.byId.get(s.photo); return ph && s.bbox ? faceCrop(ph, s.bbox, 96) : null; });
    card.append(
      el('div', { class: 'crops' }, crops),
      el('h2', { text: 'Who is this?', tabindex: '-1' }),
      candButtons(q.cands, (id) => send({ person: id })),
      picker((id) => send({ person: id })),
      el('div', { class: 'rv-actions' }, btn('Not a person', () => send({ person: 'none' })), btn('Skip', () => send({ person: 'skip' }))));
    hints.textContent = '1 to 3 choose a name. N not a person. S skip. Z undo.';
  }

  function face(q) {
    const ph = store.byId.get(q.photo);
    const first = person((q.cands || [])[0]);
    const [x, y, w, h] = q.bbox || [0, 0, 0, 0];
    card.append(
      el('div', { class: 'rv-face' },
        ph && q.bbox && faceCrop(ph, q.bbox, 240),
        ph && el('span', { class: 'rv-thumb' },
          el('img', { src: imgUrl(ph.thumb), width: ph.tw, height: ph.th, alt: ph.alt || 'The photo this face is from' }),
          el('span', { class: 'face', 'aria-hidden': 'true', style: { left: x * 100 + '%', top: y * 100 + '%', width: w * 100 + '%', height: h * 100 + '%' } }))),
      el('h2', { text: first ? `Is this ${first.name}?` : 'Who is this?', tabindex: '-1' }),
      first && el('div', { class: 'rv-actions' },
        btn('Yes', () => send({ person: first.id }), 'btn btn-primary'), btn('No', () => send({ person: 'none' }))),
      first && candButtons((q.cands || []).slice(1), (id) => send({ person: id }), 1),
      (ui.search || !first) ? picker((id) => send({ person: id })) : null,
      el('div', { class: 'rv-actions' },
        first && !ui.search && btn('Someone else', () => { ui.search = true; render(); card.querySelector('input')?.focus(); }),
        !first && btn('Not a person', () => send({ person: 'none' })),
        btn('Skip', () => send({ person: 'skip' }))));
    hints.textContent = first ? 'Y yes. N no. S skip. Z undo.' : 'S skip. Z undo.';
  }

  // Photos in a row usually share a place, so the last place you picked is always choice 3.
  function sceneOpts(q) {
    const all = q.all || store.sceneIds;
    if (!lastScene || !all.includes(lastScene)) return (q.options || []).slice(0, 3);
    return [...(q.options || []).filter((s) => s !== lastScene).slice(0, 2), lastScene];
  }

  function scene(q) {
    const ph = store.byId.get(q.photo);
    const opts = sceneOpts(q);
    card.append(
      ph && el('img', { class: 'rv-photo', src: imgUrl(ph.web), width: ph.w, height: ph.h, alt: ph.alt || 'Photo to place', style: { backgroundColor: ph.color } }),
      el('h2', { text: 'Where was this taken?', tabindex: '-1' }),
      REASON[q.reason] && el('p', { class: 'muted small', text: REASON[q.reason] }),
      el('div', { class: 'scene-options' },
        opts.map((s, i) => el('button', { type: 'button', class: 'btn', onclick: () => send({ scene: s }) }, label(s), el('span', { class: 'muted small', text: s === lastScene ? `${i + 1} · last used` : String(i + 1) }))),
        !ui.other && btn('Other...', () => { ui.other = true; render(); })),
      ui.other && el('div', { class: 'scene-options', role: 'group', 'aria-label': 'All places' },
        (q.all || store.sceneIds).map((s) => el('button', { type: 'button', class: 'chip', onclick: () => send({ scene: s }) }, label(s)))),
      el('div', { class: 'rv-actions' }, btn('Skip', () => send({ scene: 'skip' }))));
    hints.textContent = '1 to 3 choose a place. S skip. Z undo.';
  }

  function empty() {
    const status = el('p', { class: 'muted small', role: 'status' });
    const go = btn('Update results', async () => {
      go.disabled = true;
      status.textContent = 'Updating results. This can take a few minutes.';
      const r = await postJSON('/rebuild', {});
      if (!r.ok) { status.textContent = 'Results can only be updated when run with the review server.'; go.disabled = false; return; }
      try { await load(true); emit(); history.length = 0; render(); msg.textContent = 'Results updated.'; }
      catch { status.textContent = 'Results were rebuilt but the new data could not be loaded. Reload the page.'; }
    }, 'btn btn-primary');
    card.append(
      el('h2', { text: 'You are all caught up', tabindex: '-1' }),
      el('p', { class: 'muted', text: 'There are no open questions. Update results to apply your answers to the archive.' }),
      el('div', { class: 'rv-actions' }, go), status);
    hints.textContent = history.length ? 'Z undo.' : '';
  }

  function render(focus = false) {
    progress.textContent = `${answered} answered, ${store.questions.length} left`;
    msg.textContent = note;
    card.replaceChildren();
    const q = store.questions[0];
    if (!q) empty();
    else if (q.type === 'cluster') cluster(q);
    else if (q.type === 'face') face(q);
    else if (q.type === 'scene') scene(q);
    else { store.questions.shift(); render(); return; }
    if (history.length) card.append(el('div', { class: 'rv-actions' }, btn('Undo', undo)));
    if (focus) card.querySelector('h2')?.focus({ preventScroll: true });
  }

  async function send(extra) {
    const q = store.questions[0];
    if (busy || !q) return;
    busy = true;
    const r = await saveAnswer(q, extra.person ?? extra.scene, extra);
    busy = false;
    const skipped = extra.person === 'skip' || extra.scene === 'skip';
    if (extra.scene && extra.scene !== 'skip') { lastScene = extra.scene; ls.set(LAST, lastScene); }
    const counted = r.ok && !skipped;
    if (counted) { answered++; ls.set(KEY, String(answered)); }
    note = r.ok ? '' : SAVE_FAIL;
    store.questions.shift(); // optimistic: also advance when the server is absent
    history.push({ q, saved: r.ok, counted });
    ui = { search: false, other: false };
    emit();
    render(true);
  }

  async function undo() {
    const h = history[history.length - 1];
    if (!h || busy) return;
    busy = true;
    if (h.saved) {
      const r = await undoSave(h.q);
      if (!r.ok) { busy = false; note = SAVE_FAIL; render(); return; }
    }
    busy = false;
    history.pop();
    store.questions.unshift(h.q);
    if (h.counted) { answered = Math.max(0, answered - 1); ls.set(KEY, String(answered)); }
    note = '';
    ui = { search: false, other: false };
    emit();
    render(true);
  }

  function onKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    if (e.target.closest && e.target.closest('input,textarea,select')) return;
    const k = e.key.toLowerCase();
    const q = store.questions[0];
    if (k === 'z') { undo(); return; }
    if (!q) return;
    if (k === 's') send(q.type === 'scene' ? { scene: 'skip' } : { person: 'skip' });
    else if (k === 'n' && q.type !== 'scene') send({ person: 'none' });
    else if (k === 'y' && q.type === 'face' && person((q.cands || [])[0])) send({ person: q.cands[0] });
    else if (/^[1-3]$/.test(k)) {
      const i = Number(k) - 1;
      if (q.type === 'scene') { const o = sceneOpts(q); if (o[i]) send({ scene: o[i] }); }
      else if (person((q.cands || [])[i])) send({ person: q.cands[i] });
    }
  }
  document.addEventListener('keydown', onKey);
  render();
  return { title: 'Help improve results', unmount() { document.removeEventListener('keydown', onKey); } };
}
