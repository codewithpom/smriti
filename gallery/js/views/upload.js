import { el, imgUrl, plural } from '../util.js';
import { getToken } from '../auth.js';
import { imageInfo, findMatch } from '../phash.js';

const MAX_SIZE = 10 * 1024 * 1024;
const MAX_FILES = 30;
const TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const SERVER_ERRORS = {
  unauthorized: 'Please sign in again.',
  invalid_token: 'Your sign-in expired. Please sign in again.',
  profile_unavailable: 'We could not verify your account. Please try again shortly.',
  not_approved: 'Your account is not approved for photo uploads yet.',
  too_large: 'This file is too large.',
  unsupported_image: 'This image type is not supported.',
  invalid_multipart: 'The upload could not be read. Please try again.',
  invalid_file: 'The image file could not be read. Please try another copy.',
  storage_unavailable: 'Photo storage is temporarily unavailable. Your photo was not uploaded; try again shortly.',
  upload_paused_for_storage_cutover: 'Uploads are paused briefly while photo storage is being switched. Please retry shortly.',
  too_many_pending: 'Too many uploads are waiting. Try again later.',
  file_required: 'Choose a file to upload.',
};

const api = async (path, options = {}) => {
  const token = await getToken();
  if (!token) throw new Error('Please sign in again.');
  const response = await fetch(path, { ...options, headers: { authorization: `Bearer ${token}`, ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
};

function relativeTime(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'recently';
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

const fileSize = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

function scanText(scan) {
  if (!scan) return '';
  if (scan.holdReason === 'storage_migration') return 'Upload received and queued. Processing is paused during a storage migration.';
  if (scan.state === 'queued') return 'Processing will start shortly';
  if (scan.state === 'running') return `Processing: ${scan.stage || 'working'} (step ${scan.step || 1} of ${scan.steps || 1})`;
  if (scan.state === 'done') return `Last processed ${relativeTime(scan.finishedAt || scan.updatedAt || scan.createdAt)}`;
  if (scan.state === 'failed') return 'Processing failed, an admin has been notified';
  return '';
}

export function mount(root, ctx) {
  let uploads = [];
  let scan = null;
  let recent = [];
  let timer = null;
  let active = true;
  const rows = [];
  const input = el('input', { id: 'upload-files', class: 'upload-input', type: 'file', accept: 'image/jpeg,image/png,image/webp', multiple: true, 'aria-label': 'Choose JPEG, PNG, or WebP photos' });
  const live = el('p', { class: 'upload-status muted', role: 'status', 'aria-live': 'polite' });
  const list = el('div', { class: 'upload-rows' });
  const mineList = el('ul', { class: 'plain upload-mine-list' });
  const scanLine = el('p', { class: 'muted small' });
  const submit = el('button', { type: 'button', class: 'btn btn-primary', disabled: true });
  const drop = el('label', { class: 'upload-drop', for: 'upload-files' }, el('strong', { text: 'Choose photos or drop them here' }), el('span', { class: 'muted small', text: 'JPEG, PNG, or WebP · up to 10 MB each · 30 photos per batch' }), input);

  function setPolling() {
    const shouldPoll = active && document.visibilityState === 'visible' && (
      ['queued', 'running'].includes(scan?.state) || uploads.some((item) => ['queued', 'processing'].includes(item.state))
    );
    if (shouldPoll && !timer) timer = setInterval(loadMine, 10000);
    if (!shouldPoll && timer) { clearInterval(timer); timer = null; }
  }

  function statusFor(row) {
    const match = row.match;
    if (row.state === 'failed') return row.error || 'Upload failed';
    if (row.state) return { checking: 'Checking image…', uploading: 'Uploading…', uploaded: 'Uploaded' }[row.state] || row.state;
    if (row.error) return row.error;
    if (!match) return row.info ? 'Ready to upload' : 'Checking image…';
    if (match.kind === 'duplicate') return match.batch ? 'Same as another photo in this batch' : 'Already in the archive';
    return 'Looks similar to a photo in the archive';
  }

  function renderRows() {
    list.replaceChildren(...rows.map((row) => {
      const target = row.match?.id && !row.match.batch
        ? (ctx.store.photos.find((photo) => photo.id === row.match.id) || recent.find((photo) => (photo.photo || photo.id) === row.match.id)) : null;
      const thumb = target?.thumb ? el('a', { href: `#/photo/${target.id}`, 'aria-label': 'Open matching photo' }, el('img', { class: 'upload-match-thumb', src: imgUrl(target.thumb), alt: '' })) : null;
      const toggle = row.match && !row.error ? el('label', { class: 'upload-choice' }, el('input', { type: 'checkbox', checked: row.include, onchange: (event) => { row.include = event.target.checked; updateSubmit(); } }), row.match.kind === 'duplicate' ? 'Upload anyway' : 'Skip this photo') : null;
      const action = row.state === 'failed' ? el('button', { type: 'button', class: 'btn btn-quiet', onclick: () => uploadRow(row) }, 'Retry') : null;
      return el('article', { class: 'upload-row' }, el('img', { class: 'upload-file-thumb', src: row.url, alt: '', width: '52', height: '52' }),
        el('div', { class: 'upload-file-info' }, el('strong', { text: row.file.name }), el('span', { class: 'muted small', text: `${fileSize(row.file.size)} · ${statusFor(row)}` }),
          thumb, toggle, action));
    }));
    updateSubmit();
  }

  function updateSubmit() {
    const count = rows.filter((row) => row.include && !row.state?.startsWith('uploaded') && row.state !== 'uploading' && !row.error).length;
    submit.textContent = `Upload ${plural(count, 'photo')}`;
    submit.disabled = count === 0 || rows.some((row) => row.state === 'checking' || row.state === 'uploading');
  }

  async function choose(files) {
    const chosen = [...files];
    if (!chosen.length) return;
    if (chosen.length > MAX_FILES) { live.textContent = 'Choose no more than 30 photos at a time.'; return; }
    const accepted = [];
    for (const file of chosen) {
      if (!TYPES.has(file.type)) { live.textContent = `${file.name}: choose a JPEG, PNG, or WebP image.`; continue; }
      if (file.size > MAX_SIZE) { live.textContent = `${file.name}: the photo must be 10 MB or smaller.`; continue; }
      const row = { file, url: URL.createObjectURL(file), info: null, match: null, include: true, state: 'checking', error: '' };
      rows.push(row); accepted.push(row);
    }
    if (rows.length > MAX_FILES) {
      for (const row of rows.splice(MAX_FILES)) URL.revokeObjectURL(row.url);
      live.textContent = 'Choose no more than 30 photos at a time.';
    }
    renderRows();
    const archive = ctx.store.photos.filter((item) => item.ph && item.w && item.h).map(({ id, ph, w, h }) => ({ id, sha: '', ph, w, h }));
    for (const row of accepted) {
      try {
        row.info = await imageInfo(row.file);
        const ownIndex = recent.filter((item) => item.ph).map((item) => ({ id: item.photo || item.id, sha: item.sha || '', ph: item.ph, w: item.w, h: item.h }));
        const earlier = rows.slice(0, rows.indexOf(row)).filter((item) => item.info)
          .map((item, index) => ({ id: `batch-${index}`, sha: item.info.sha, ph: item.info.ph, w: item.info.w, h: item.info.h }));
        const match = findMatch(row.info, [...archive, ...ownIndex, ...earlier]);
        if (match) {
          row.match = { kind: match[0], id: match[1], batch: String(match[1]).startsWith('batch-') };
          row.include = match[0] !== 'duplicate';
        }
        row.state = '';
      } catch {
        row.state = '';
        row.error = 'Can’t read this image';
        row.include = false;
      }
      renderRows();
    }
  }

  async function uploadRow(row) {
    row.state = 'uploading'; row.error = ''; renderRows();
    try {
      const token = await getToken();
      if (!token) throw new Error('Please sign in again.');
      const form = new FormData(); form.append('file', row.file);
      const response = await fetch('/api/upload', { method: 'POST', headers: { authorization: `Bearer ${token}` }, body: form });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(SERVER_ERRORS[result.error] || 'Upload failed. Please try again.');
      row.state = 'uploaded';
    } catch (error) { row.state = 'failed'; row.error = error.message || 'Upload failed. Please try again.'; }
    renderRows();
    if (row.state === 'uploaded') { live.textContent = `${row.file.name} uploaded.`; await loadMine(); }
  }

  async function uploadSelected() {
    const pending = rows.filter((row) => row.include && row.state !== 'uploaded' && row.state !== 'uploading' && !row.error);
    for (const row of pending) await uploadRow(row);
  }

  function renderMine() {
    scanLine.textContent = scanText(scan);
    const states = {
      queued: 'Waiting to be processed', processing: 'Processing', awaiting_approval: 'Waiting for admin approval',
      live: 'Live', duplicate: 'Duplicate', rejected: 'Not approved', unreadable: 'Could not read',
    };
    mineList.replaceChildren(...uploads.map((item) => { // the API returns them newest first
      const target = item.state === 'duplicate' ? item.dupOf : item.state === 'live' ? item.photo : null;
      const label = states[item.state] || 'Upload status unavailable';
      return el('li', {}, el('span', { class: 'upload-mine-name', text: `Uploaded ${relativeTime(item.at)}` }),
        el('span', { class: 'pill', text: label }), target ? el('a', { href: `#/photo/${target}`, class: 'btn btn-quiet' }, item.state === 'duplicate' ? 'View original' : 'View photo') : null);
    }));
    setPolling();
  }

  async function loadMine() {
    if (!active) return;
    try {
      const data = await api('/api/uploads/mine');
      uploads = data.items || data.uploads || [];
      scan = data.scan || null;
      recent = uploads;
      renderMine();
    } catch (error) { live.textContent = error.message || 'Could not load your uploads.'; setPolling(); }
  }

  input.addEventListener('change', () => { choose(input.files || []); input.value = ''; });
  drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('is-dragging'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('is-dragging'));
  drop.addEventListener('drop', (event) => { event.preventDefault(); drop.classList.remove('is-dragging'); choose(event.dataTransfer.files); });
  submit.addEventListener('click', uploadSelected);
  const onVisible = () => { if (document.visibilityState === 'visible') loadMine(); else setPolling(); };
  document.addEventListener('visibilitychange', onVisible);
  root.append(el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Upload photos' }), el('p', { text: 'Share photos for review by the archive team.' }))),
    el('section', { class: 'upload-form', 'aria-label': 'Upload photos' }, drop, list, submit, live),
    el('section', { class: 'upload-mine' }, el('h2', { text: 'Your uploads' }), scanLine, mineList));
  loadMine();
  return { title: 'Upload photos', unmount() { active = false; if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); rows.forEach((row) => URL.revokeObjectURL(row.url)); } };
}
