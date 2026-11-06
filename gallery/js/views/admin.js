import { el } from '../util.js';
import { getToken, isAdmin, currentProfile } from '../auth.js';
import { getProgress, listAllVotes, listOverrides, listUsers, saveOverride, saveProgress, setUserStatus } from '../admin-data.js';

const TABS = ['Users', 'Disputes', 'Uploads', 'Stats', 'Export'];
const api = async (path, options = {}) => {
  const token = await getToken();
  if (!token) throw new Error('Please sign in again.');
  const response = await fetch(`/api/admin/${path}`, { ...options, headers: { authorization: `Bearer ${token}`, ...(options.body ? { 'content-type': 'application/json' } : {}), ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
};

export function tally(questions, votes, users) {
  const approved = new Map(users.filter((u) => u.status === 'approved').map((u) => [u.uid, u]));
  const settled = {}, disputed = [], faces = {}, scenes = {};
  for (const q of questions) {
    const weights = new Map();
    for (const vote of votes.filter((v) => v.qid === q.id && v.answer !== 'skip' && approved.has(v.uid))) {
      const voter = approved.get(vote.uid);
      const weight = (q.type === 'face' || q.type === 'cluster') && voter.pid === vote.answer && (q.cands || []).includes(voter.pid) ? 2 : 1;
      weights.set(vote.answer, (weights.get(vote.answer) || 0) + weight);
    }
    const ranked = [...weights].sort((a, b) => b[1] - a[1]);
    const [answer, top = 0] = ranked[0] || [];
    const second = ranked[1]?.[1] || 0;
    if (top >= 3 && top >= 2 * second) {
      settled[q.id] = true;
      if (q.type === 'scene') scenes[q.photo] = answer;
      else if (answer !== 'none') for (const key of q.type === 'cluster' ? q.faces : [`${q.photo}:${q.i}`]) faces[key] = answer;
      else for (const key of q.type === 'cluster' ? q.faces : [`${q.photo}:${q.i}`]) faces[key] = 'none';
    } else if ([...weights.values()].reduce((a, n) => a + n, 0) >= 5) disputed.push(q.id);
  }
  return { faces, scenes, skipped: [], history: [], settled, disputed };
}

export function mount(root) {
  let tab = 'Users';
  const status = el('p', { class: 'admin-status muted', role: 'status', 'aria-live': 'polite' });
  const body = el('section', { class: 'admin-panel', 'aria-live': 'polite' });
  const nav = el('div', { class: 'admin-tabs', role: 'tablist', 'aria-label': 'Administration' });
  const buttons = TABS.map((name) => el('button', { type: 'button', role: 'tab', class: 'chip', 'aria-selected': 'false', onclick: () => { tab = name; render(); } }, name));
  nav.append(...buttons);
  root.append(el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Administration' }), el('p', { text: 'Manage review activity and export corrections.' }))), nav, status, body);

  const button = (text, action, cls = 'btn') => el('button', { type: 'button', class: cls, onclick: action }, text);
  async function render() {
    buttons.forEach((b, i) => b.setAttribute('aria-selected', String(TABS[i] === tab)));
    body.replaceChildren(); status.textContent = 'Loading…';
    try {
      if (!(await isAdmin())) throw new Error('Administrator access is required.');
      await currentProfile();
      if (tab === 'Uploads') await uploads();
      else if (tab === 'Users') await users();
      else if (tab === 'Disputes') await disputes();
      else if (tab === 'Stats') await stats();
      else await exportPanel();
      status.textContent = '';
    } catch (error) { status.textContent = error.message || 'Could not load administration data.'; }
  }
  async function users() {
    const list = await listUsers();
    body.append(el('h2', { text: 'Users' }), el('ul', { class: 'plain admin-list' }, list.map((u) => el('li', {},
      el('span', { class: 'admin-main' }, el('strong', { text: u.email || u.uid }), el('span', { class: 'muted small', text: `${u.pid || ''} · ${u.status}` })),
      u.status !== 'approved' && button('Approve', () => changeUser(u.uid, 'approved')),
      u.status !== 'blocked' && button('Block', () => changeUser(u.uid, 'blocked'), 'btn btn-quiet')))));
  }
  async function changeUser(uid, statusValue) {
    try { await setUserStatus(uid, statusValue); status.textContent = 'User updated.'; await render(); }
    catch (e) { status.textContent = e.message; }
  }
  async function disputes() {
    const [{ disputed = [], settled = {} }, votes, overrides] = await Promise.all([getProgress(), listAllVotes(), listOverrides()]);
    const counts = new Map();
    for (const vote of votes) if (disputed.includes(vote.qid)) counts.set(vote.qid, (counts.get(vote.qid) || 0) + 1);
    body.append(el('h2', { text: 'Disputes' }));
    for (const qid of disputed) {
      const answer = el('input', { class: 'input', type: 'text', maxlength: '64', 'aria-label': `Chosen answer for ${qid}`, value: overrides[qid] || '' });
      body.append(el('article', { class: 'admin-row' }, el('strong', { text: qid }), el('span', { class: 'muted small', text: `${counts.get(qid) || 0} votes` }), answer,
        button('Set answer', async () => { try { await saveOverride(qid, answer.value); await saveProgress({ settled: { ...settled, [qid]: true }, disputed: disputed.filter((id) => id !== qid) }); status.textContent = 'Answer saved.'; await render(); } catch (e) { status.textContent = e.message; } })));
    }
    if (!disputed.length) body.append(el('p', { class: 'muted', text: 'No disputed questions.' }));
  }
  async function uploads() {
    const { uploads: list = [] } = await api('uploads');
    for (const item of list) {
      const preview = el('img', { class: 'admin-preview', alt: `Pending upload ${item.key}`, loading: 'lazy' });
      const token = await getToken();
      const response = await fetch(`/api/admin/upload-image?key=${encodeURIComponent(item.key)}`, { headers: { authorization: `Bearer ${token}` } });
      if (response.ok) preview.src = URL.createObjectURL(await response.blob());
      body.append(el('article', { class: 'admin-row' }, preview, el('span', { class: 'admin-main', text: `${item.key} · ${item.size} bytes` }),
        button('Approve', () => moderate(item.key, 'approve')), button('Reject', () => moderate(item.key, 'reject'), 'btn btn-quiet')));
    }
    if (!list.length) body.append(el('p', { class: 'muted', text: 'No pending uploads.' }));
  }
  async function moderate(key, action) {
    try { await api('uploads', { method: 'POST', body: JSON.stringify({ key, action }) }); status.textContent = 'Upload updated.'; await render(); }
    catch (e) { status.textContent = e.message; }
  }
  async function stats() {
    const [list, votes] = await Promise.all([listUsers(), listAllVotes()]);
    const counts = new Map();
    votes.forEach((vote) => counts.set(vote.uid, (counts.get(vote.uid) || 0) + 1));
    body.append(el('h2', { text: 'Votes per student' }), el('ul', { class: 'plain admin-list' }, list.map((u) => el('li', {},
      el('span', { class: 'admin-main', text: u.email || u.uid }), el('span', { class: 'pill', text: String(counts.get(u.uid) || 0) })))))
  }
  async function exportPanel() {
    const token = await getToken();
    const response = await fetch('/api/data/questions', { headers: { authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error('Could not load questions for export.');
    const questions = await response.json();
    const [votes, list, overrides] = await Promise.all([listAllVotes(), listUsers(), listOverrides()]);
    const result = tally(questions, votes, list);
    Object.assign(result.settled, overrides);
    const faces = {}, scenes = {};
    for (const q of questions) {
      const answer = result.settled[q.id];
      if (!answer || answer === 'skip') continue;
      if (q.type === 'scene') scenes[q.photo] = answer;
      else for (const key of q.type === 'cluster' ? q.faces : [`${q.photo}:${q.i}`]) faces[key] = answer;
    }
    const link = el('a', { class: 'btn btn-primary', href: URL.createObjectURL(new Blob([JSON.stringify({ faces, scenes, skipped: [], history: [] }, null, 2)], { type: 'application/json' })), download: 'corrections.json', text: 'Download corrections.json' });
    body.append(el('h2', { text: 'Export corrections' }), el('p', { class: 'muted', text: 'Apply the approved vote tally and download the correction file.' }), link);
  }
  render();
  return { title: 'Administration', unmount() { body.replaceChildren(); } };
}
