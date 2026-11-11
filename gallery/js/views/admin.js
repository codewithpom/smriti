import { el, imgUrl } from '../util.js';
import { getToken, isAdmin, currentProfile } from '../auth.js';
import { getProgress, listAllVotes, listOverrides, listUsers, saveOverride, saveProgress, setUserStatus, seedRoster, addRoster, approveUsers, listExtraPeople, updateExtraPerson, deleteExtraPerson, approveExtraPerson, getApprovalMode, setApprovalMode } from '../admin-data.js';

const TABS = ['Users', 'Disputes', 'Uploads', 'Ingest', 'Stats', 'New people', 'Export'];
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

export function mount(root, ctx) {
  let tab = 'Users';
  const status = el('p', { class: 'admin-status muted', role: 'status', 'aria-live': 'polite' });
  const body = el('section', { class: 'admin-panel', id: 'admin-panel', role: 'tabpanel' });
  const nav = el('div', { class: 'admin-tabs', role: 'tablist', 'aria-label': 'Administration' });
  const buttons = TABS.map((name) => el('button', { type: 'button', role: 'tab', class: 'chip', 'aria-selected': 'false', 'aria-controls': 'admin-panel', onclick: () => { tab = name; render(); } }, name));
  nav.addEventListener('keydown', (e) => {
    const i = buttons.indexOf(document.activeElement), d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (i < 0 || !d) return;
    e.preventDefault(); buttons[(i + d + buttons.length) % buttons.length].focus();
  });
  nav.append(...buttons);
  root.append(el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Administration' }), el('p', { text: 'Manage review activity and export corrections.' }))), nav, status, body);

  const button = (text, action, cls = 'btn') => el('button', { type: 'button', class: cls, onclick: action }, text);
  const relative = (value) => {
    const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
    if (!Number.isFinite(minutes)) return 'recently';
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
    return `${Math.floor(minutes / 1440)}d ago`;
  };
  const scanLine = (scan) => {
    if (scan?.state === 'queued') return 'Processing will start shortly';
    if (scan?.state === 'running') return `Processing: ${scan.stage || 'working'} (step ${scan.step || 1} of ${scan.steps || 1})`;
    if (scan?.state === 'done') return `Last processed ${relative(scan.finishedAt || scan.updatedAt || scan.createdAt)}`;
    if (scan?.state === 'failed') return 'Processing failed, an admin has been notified';
    return 'No processing run is active.';
  };
  const adminMine = async () => {
    const token = await getToken();
    const response = await fetch('/api/uploads/mine', { headers: { authorization: `Bearer ${token}` } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not load processing status.');
    return data;
  };
  async function render() {
    buttons.forEach((b, i) => { b.setAttribute('aria-selected', String(TABS[i] === tab)); b.tabIndex = TABS[i] === tab ? 0 : -1; });
    body.replaceChildren(); status.textContent = 'Loading…';
    try {
      if (!(await isAdmin())) throw new Error('Administrator access is required.');
      await currentProfile();
      if (tab === 'Uploads') await uploads();
      else if (tab === 'Ingest') await ingest();
      else if (tab === 'Users') await users();
      else if (tab === 'Disputes') await disputes();
      else if (tab === 'New people') await newPeople();
      else if (tab === 'Stats') await stats();
      else await exportPanel();
      status.textContent = '';
    } catch (error) { status.textContent = error.message || 'Could not load administration data.'; }
  }
  async function users() {
    const list = await listUsers();
    const pending = list.filter((u) => u.status === 'pending');
    const mode = await getApprovalMode();
    const toggle = button(mode === 'admin' ? 'Approval: admin approves new students' : 'Approval: automatic (roster is the gate)', async () => {
      const next = mode === 'admin' ? 'auto' : 'admin';
      try {
        await setApprovalMode(next);
        await render();
        status.textContent = next === 'admin' ? 'New students will wait for your approval.' : 'New students are approved automatically.'; // after render, which clears status
      } catch (e) { status.textContent = e.message; }
    }, 'btn btn-quiet');
    let armed = false;
    const approveAll = button(`Approve all pending (${pending.length})`, async () => {
      if (!pending.length) return;
      if (!armed) { armed = true; approveAll.textContent = `Click again to approve ${pending.length} users`; return; }
      try { await approveUsers(pending.map((u) => u.uid)); status.textContent = `${pending.length} users approved.`; await render(); }
      catch (e) { status.textContent = e.message; }
    });
    const seed = button('Import roster from gallery data', async () => {
      try {
        const token = await getToken();
        const r = await fetch('/api/data/people', { headers: { authorization: `Bearer ${token}` } });
        if (!r.ok) throw new Error(`Could not load the people list (${r.status}).`);
        status.textContent = `Roster imported: ${await seedRoster(await r.json())} students.`;
      } catch (e) { status.textContent = e.message; }
    }, 'btn btn-quiet');
    const emails = el('textarea', { class: 'input', rows: '3', 'aria-label': 'Student emails to add', placeholder: 'rkmv1234@gmail.com, rkmv5678@gmail.com' });
    const addBtn = button('Add students', async () => {
      try {
        const { added, skipped } = await addRoster(emails.value);
        status.textContent = `${added} student${added === 1 ? '' : 's'} can now sign in.${skipped ? ` Skipped ${skipped} that are not rkmv<number>@gmail.com.` : ''}`;
        if (added) emails.value = '';
      } catch (e) { status.textContent = e.message; }
    });
    body.append(el('h2', { text: 'Users' }), toggle, el('div', { class: 'rv-actions' }, seed, approveAll),
      el('label', { class: 'muted small', text: 'Add more students (one email per line, or comma-separated)' }), emails, el('div', { class: 'rv-actions' }, addBtn), el('ul', { class: 'plain admin-list' }, list.map((u) => el('li', {},
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
    const { items = [], queued = 0 } = await api('uploads');
    body.append(el('h2', { text: 'Uploads' }));
    if (queued) body.append(el('p', { class: 'pill', text: `${queued} uploads waiting to be processed` }));
    for (const item of items) {
      const faces = (item.faces || []).map((face) => {
        const person = ctx?.store?.people?.find((entry) => entry.id === (face.person || face.personId));
        return person?.name || 'Unknown person';
      });
      let armed = false;
      const reject = button('Reject', async () => {
        if (!armed) { armed = true; reject.textContent = 'Click again to reject'; return; }
        await moderate(item.photo, 'reject');
      }, 'btn btn-quiet');
      body.append(el('article', { class: 'admin-row' }, el('img', { class: 'admin-preview', src: imgUrl(item.thumb), alt: 'Processed upload thumbnail', loading: 'lazy' }),
        el('span', { class: 'admin-main' }, el('strong', { text: `Uploader ${String(item.uploader || item.uid || 'unknown').slice(0, 8)}` }),
          el('span', { class: 'muted small', text: relative(item.at || item.createdAt || item.uploadedAt) }),
          el('span', { class: 'muted small', text: faces.length ? faces.join(', ') : 'No faces identified' }),
          item.dupOf && el('span', { class: 'pill', text: `Duplicate of ${item.dupOf}` })),
        button('Approve', () => moderate(item.photo, 'approve'), 'btn btn-primary'), reject));
    }
    if (!items.length) body.append(el('p', { class: 'muted', text: 'No uploads need review.' }));
  }
  async function moderate(photo, action) {
    try {
      await api('uploads', { method: 'POST', body: JSON.stringify({ photo, action }) });
      await render();
      status.textContent = action === 'approve' ? 'Approved. It will appear after the next processing run.' : 'Rejected.';
    } catch (e) { status.textContent = e.message; }
  }
  async function ingest() {
    const [cookies, run, mine, storage] = await Promise.all([api('fb-cookies').catch(() => ({})), api('scan').catch((e) => ({ error: e.message })), adminMine().catch(() => ({})), api('storage').catch(() => ({}))]);
    const processingLine = el('p', { class: 'muted', text: scanLine(mine.scan) });
    const refresh = button('Refresh', async () => { try { processingLine.textContent = scanLine((await adminMine()).scan); } catch (error) { status.textContent = error.message; } }, 'btn btn-quiet');
    const note = el('p', { class: 'muted small', text: 'Cookies are write-only: they are stored in the private bucket, used by the scan job, and never shown again. Use a throwaway account, not your own.' });
    const have = cookies.present ? `Saved (${cookies.bytes} bytes, ${cookies.updated ? new Date(cookies.updated).toLocaleString() : 'unknown date'}).` : 'No cookies saved.';
    const box = el('textarea', { class: 'input', rows: '5', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Facebook cookies', placeholder: 'Paste cookies.txt (Netscape) or the JSON export from a cookie extension' });
    const url = el('input', { class: 'input', type: 'url', autocomplete: 'off', 'aria-label': 'Facebook page photos URL', placeholder: 'https://www.facebook.com/<page>/photos' });
    const save = button('Save cookies', async () => {
      try {
        const res = await api('fb-cookies', { method: 'POST', body: JSON.stringify({ cookies: box.value, ...(url.value.trim() ? { pageUrl: url.value.trim() } : {}) }) });
        box.value = ''; await render(); status.textContent = `Saved ${res.count} Facebook cookies.`;
      } catch (e) { status.textContent = 'Could not save: check the cookies include the logged-in session (c_user and xs).'; }
    });
    let armed = false;
    const clear = button('Delete saved cookies', async () => {
      if (!armed) { armed = true; clear.textContent = 'Click again to delete'; return; }
      try { await api('fb-cookies', { method: 'DELETE' }); await render(); status.textContent = 'Cookies deleted.'; } catch (e) { status.textContent = e.message; }
    }, 'btn btn-quiet');
    const fetchFb = el('input', { type: 'checkbox', disabled: !cookies.present });
    const recluster = el('input', { type: 'checkbox' });
    const reembed = el('input', { type: 'checkbox' });
    const go = button('Run scan now', async () => {
      try {
        await api('scan', { method: 'POST', body: JSON.stringify({ fetchFacebook: fetchFb.checked, recluster: recluster.checked, reembed: reembed.checked }) });
        await render(); status.textContent = 'Scan started. It takes a while; refresh this tab to see progress.';
      } catch (e) { status.textContent = e.message === 'not_configured' ? 'Set GITHUB_DISPATCH_TOKEN and GITHUB_REPO first.' : e.message; }
    });
    const last = run.none ? 'No runs yet.' : run.error ? 'Run status unavailable.' : `Latest run: ${run.status}${run.conclusion ? ` (${run.conclusion})` : ''}${run.createdAt ? `, ${new Date(run.createdAt).toLocaleString()}` : ''}.`;
    const open = 'New faces that match nobody appear in Review as groups. Name a group once and every face in it is labelled.';
    body.append(el('h2', { text: 'Processing' }), processingLine, refresh, el('h2', { text: 'Facebook ingest' }), note, el('p', { text: have }), box,
      el('label', { class: 'muted small', text: 'Page photos URL (optional, saved with the cookies)' }), url,
      el('div', { class: 'rv-actions' }, save, cookies.present && clear),
      el('h2', { text: 'Photo scan' }), el('p', { class: 'muted small', text: 'Runs on GitHub Actions: downloads new photos, finds faces, groups unknown faces into unnamed people, rebuilds the data.' }),
      el('label', { class: 'check' }, fetchFb, ' Fetch new Facebook photos first'),
      el('label', { class: 'check' }, recluster, ' Re-group unnamed faces from scratch'),
      el('label', { class: 'check' }, reembed, ' Re-run face recognition on every photo (needs R2, slow)'),
      el('div', { class: 'rv-actions' }, go), el('p', { class: 'muted', text: last }),
      el('h2', { text: 'Unnamed people' }), el('p', { class: 'muted small', text: open }),
      el('a', { class: 'btn', href: '#/review' }, 'Name people in Review'));
    storagePanel(storage);
  }
  function storagePanel(data) {
    const backend = data.setting || data.binding || 'kv';
    const selected = el('input', { type: 'radio', name: 'storage', value: 'kv', checked: backend === 'kv' });
    const r2 = el('input', { type: 'radio', name: 'storage', value: 'r2', checked: backend === 'r2' });
    const choices = el('fieldset', { class: 'storage-options' }, el('legend', { text: 'Storage' }),
      el('label', {}, selected, ' Workers KV: free, no card needed, 1 GB, about 1,000 writes a day, no original files kept'),
      el('label', {}, r2, ' R2: free up to 10 GB, needs a card on file, keeps original files'));
    const usage = data.usage || {};
    const note = el('p', { class: 'muted small', text: `${usage.keys ?? 0} keys \u00b7 ${(Number(usage.bytes || 0) / 1024 / 1024).toFixed(1)} MB` });
    const warning = data.setting && data.setting !== data.binding
      ? el('p', { class: 'pill', text: `Website is still connected to ${data.binding}` }) : null;
    const next = el('div', { class: 'storage-next' });
    const switchButton = button('Switch storage', async () => {
      const target = choices.querySelector('input:checked')?.value;
      if (!target || target === backend) { status.textContent = 'Choose a different storage backend first.'; return; }
      try {
        await api('storage', { method: 'POST', body: JSON.stringify({ backend: target }) });
        const old = backend;
        next.replaceChildren(el('ol', {},
          el('li', { text: `In Cloudflare Pages settings, bind the ${target.toUpperCase()} store as PHOTOS.` }),
          el('li', { text: target === 'r2' ? 'In GitHub, add R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET secrets.' : 'In GitHub, add the CF_KV_NAMESPACE_ID secret.' }),
          el('li', {}, button('Copy data now', async () => {
            try {
              await api('scan', { method: 'POST', body: JSON.stringify({ migrateFrom: old }) });
              status.textContent = 'Data copy started.';
            } catch (error) {
              status.textContent = error.message === 'not_configured' ? 'Set GITHUB_DISPATCH_TOKEN and GITHUB_REPO first.' : error.message;
            }
          }, 'btn btn-primary'))));
        status.textContent = 'Storage setting updated. Complete these steps in order.';
      } catch (error) { status.textContent = error.message === 'not_configured' ? 'Set GITHUB_DISPATCH_TOKEN and GITHUB_REPO first.' : error.message; }
    }, 'btn btn-primary');
    body.append(choices, note, warning, switchButton, next);
  }
  async function stats() {
    const [list, votes] = await Promise.all([listUsers(), listAllVotes()]);
    const counts = new Map();
    votes.forEach((vote) => counts.set(vote.uid, (counts.get(vote.uid) || 0) + 1));
    body.append(el('h2', { text: 'Votes per student' }), el('ul', { class: 'plain admin-list' }, list.map((u) => el('li', {},
      el('span', { class: 'admin-main', text: u.email || u.uid }), el('span', { class: 'pill', text: String(counts.get(u.uid) || 0) })))))
  }
  async function newPeople() {
    const list = (await listExtraPeople()).sort((a, b) => a.reg_no.localeCompare(b.reg_no));
    body.append(el('h2', { text: 'New people' }), el('p', { class: 'muted small', text: 'Students add people as pending; approve them to show everyone and include them in the download.' }));
    body.append(button('Download for the pipeline', () => {
      const data = list.filter((p) => p.status === 'approved').map(({ reg_no, name, batch }) => ({ reg_no, name, batch }));
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = el('a', { href: url, download: 'extra_people.json' });
      body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
    for (const p of list) {
      const name = el('input', { class: 'input', maxlength: '60', value: p.name, 'aria-label': `Name for ${p.reg_no}` });
      let armed = false;
      const remove = button('Delete', async () => {
        if (!armed) { armed = true; remove.textContent = 'Click again to delete'; return; }
        try { await deleteExtraPerson(p.id); await render(); }
        catch (e) { status.textContent = e.message; }
      }, 'btn btn-quiet');
      body.append(el('article', { class: 'admin-row' }, el('strong', { text: p.reg_no }), name,
        button('Save', async () => {
          try { await updateExtraPerson(p.id, name.value); p.name = name.value; status.textContent = 'Name saved.'; }
          catch (e) { status.textContent = e.message; }
        }), el('span', { class: 'muted', text: p.batch }), el('span', { class: 'pill', text: p.status || 'legacy' }),
        p.status !== 'approved' && button('Approve', async () => {
          try { await approveExtraPerson(p.id); await render(); status.textContent = 'Person approved.'; }
          catch (e) { status.textContent = e.message; }
        }), remove));
    }
    if (!list.length) body.append(el('p', { class: 'muted', text: 'No new people.' }));
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
    const link = el('a', { class: 'btn btn-primary', download: 'corrections.json', text: 'Download corrections.json' });
    const corrections = { faces, scenes, skipped: [], history: [] };
    link.href = URL.createObjectURL(new Blob([JSON.stringify(corrections, null, 2)], { type: 'application/json' }));
    const send = button('Send to the pipeline', async () => {
      try {
        await api('corrections', { method: 'POST', body: JSON.stringify(corrections) });
        status.textContent = 'Sent. The next processing run applies it.';
      } catch (error) { status.textContent = error.message || 'Could not send corrections.'; }
    });
    body.append(el('h2', { text: 'Export corrections' }), el('p', { class: 'muted', text: 'Apply the approved vote tally and download the correction file.' }), el('div', { class: 'rv-actions' }, link, send));
  }
  render();
  return { title: 'Administration', unmount() { body.replaceChildren(); } };
}
