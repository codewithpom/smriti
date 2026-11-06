import { el } from '../util.js';
import { getToken } from '../auth.js';

const MAX = 10 * 1024 * 1024;
const TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export function mount(root) {
  const file = el('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp', 'aria-label': 'Choose a JPEG, PNG, or WebP photo' });
  const submit = el('button', { type: 'submit', class: 'btn btn-primary' }, 'Upload photo');
  const status = el('p', { class: 'upload-status muted', role: 'status', 'aria-live': 'polite' });
  const form = el('form', { class: 'upload-form' }, el('label', {}, 'Photo file (JPEG, PNG, or WebP; up to 10 MB)', file), submit, status);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const chosen = file.files?.[0];
    if (!chosen) { status.textContent = 'Choose a photo first.'; return; }
    if (!TYPES.has(chosen.type)) { status.textContent = 'Choose a JPEG, PNG, or WebP image.'; return; }
    if (chosen.size > MAX) { status.textContent = 'The photo must be 10 MB or smaller.'; return; }
    submit.disabled = true; status.textContent = 'Uploading…';
    try {
      const token = await getToken();
      if (!token) throw new Error('Please sign in again.');
      const data = new FormData(); data.append('file', chosen);
      const response = await fetch('/api/upload', { method: 'POST', headers: { authorization: `Bearer ${token}` }, body: data });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.error || `Upload failed (${response.status}).`);
      status.textContent = 'Photo uploaded. It is waiting for review.'; form.reset();
    } catch (error) { status.textContent = error.message || 'Upload failed. Please try again.'; }
    finally { submit.disabled = false; }
  });
  root.append(el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Upload a photo' }), el('p', { text: 'Share a photo for review by the archive team.' }))), form);
  return { title: 'Upload a photo', unmount() { form.reset(); } };
}
