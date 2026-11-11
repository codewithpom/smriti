// Signed-out landing page. Static markup only (no data is interpolated), and no real photo ever appears here:
// the art is drawn SVG, so nothing public can leak a student's face.
import { el } from './util.js';

const HERO = `
<svg class="ld-art" viewBox="0 0 420 360" role="img" aria-label="Illustration of a stack of instant photos with a face being recognised">
  <defs>
    <linearGradient id="ld-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--ld-sky-a)"/><stop offset="1" stop-color="var(--ld-sky-b)"/></linearGradient>
    <linearGradient id="ld-dusk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--ld-dusk-a)"/><stop offset="1" stop-color="var(--ld-dusk-b)"/></linearGradient>
  </defs>
  <g class="ld-card ld-c3"><rect x="40" y="52" width="170" height="210" rx="10" class="ld-paper"/><rect x="52" y="64" width="146" height="150" rx="5" fill="url(#ld-dusk)"/><circle cx="150" cy="104" r="18" class="ld-sun"/><path d="M52 190c28-34 52-34 76-8 22-24 44-26 70-4v36H52z" class="ld-hill2"/></g>
  <g class="ld-card ld-c2"><rect x="214" y="40" width="170" height="210" rx="10" class="ld-paper"/><rect x="226" y="52" width="146" height="150" rx="5" fill="url(#ld-sky)"/><path d="M226 170l36-48 28 30 22-22 60 40v22H226z" class="ld-hill1"/><circle cx="334" cy="80" r="11" class="ld-sun"/></g>
  <g class="ld-card ld-c1">
    <rect x="112" y="92" width="196" height="240" rx="12" class="ld-paper ld-front"/>
    <rect x="126" y="106" width="168" height="172" rx="6" class="ld-frame"/>
    <g class="ld-people">
      <circle cx="170" cy="188" r="17" class="ld-face f1"/><path d="M142 262c2-30 14-44 28-44s26 14 28 44z" class="ld-body f1"/>
      <circle cx="222" cy="176" r="19" class="ld-face f2"/><path d="M192 262c2-34 16-50 30-50s28 16 30 50z" class="ld-body f2"/>
      <circle cx="262" cy="192" r="16" class="ld-face f3"/><path d="M236 262c2-28 12-40 26-40s24 12 26 40z" class="ld-body f3"/>
    </g>
    <rect class="ld-box" x="198" y="150" width="48" height="52" rx="6" pathLength="100"/>
    <g class="ld-tag"><rect x="190" y="206" width="64" height="20" rx="10"/><text x="222" y="220" text-anchor="middle">Who is this?</text></g>
    <rect x="140" y="290" width="90" height="8" rx="4" class="ld-line"/><rect x="140" y="306" width="56" height="6" rx="3" class="ld-line ld-line2"/>
  </g>
  <g class="ld-spark"><path d="M368 300l4 10 10 4-10 4-4 10-4-10-10-4 10-4z"/><path d="M60 300l3 7 7 3-7 3-3 7-3-7-7-3 7-3z" class="s2"/></g>
</svg>`;

const ICON = {
  faces: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="9" r="3.2"/><circle cx="16.5" cy="10.5" r="2.4"/><path d="M3.5 19c.4-3.6 2.7-5.5 5.5-5.5s5.1 1.9 5.5 5.5M14.5 14.2c3 0 5 1.6 5.5 4.8"/></svg>',
  days: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><circle cx="9" cy="14.5" r="1"/><circle cx="13" cy="14.5" r="1"/></svg>',
  lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10.5" width="14" height="9.5" rx="2.5"/><path d="M8 10.5V8a4 4 0 018 0v2.5"/><circle cx="12" cy="15.2" r="1.2"/></svg>',
};

const CARDS = [
  ['faces', 'Find yourself', 'Faces are grouped automatically, so every photo you appear in is one tap away. Name the people you know and the rest follows.'],
  ['days', 'Relive the days', 'Browse by event, place and date. Each visit shuffles the feed, so you keep running into photos you forgot.'],
  ['lock', 'Private by design', 'Nothing here is public. Photos, names and faces only load after you sign in, and only for the batch roster.'],
];

const STEPS = [
  ['Sign in', 'Use the Google account that matches your registration number.'],
  ['Wait for approval', 'An admin approves new accounts. This usually takes a day.'],
  ['Browse', 'Photos load as soon as you are approved.'],
];

const html = (s) => { const t = document.createElement('template'); t.innerHTML = s.trim(); return t.content.firstElementChild; };

export function renderLanding(view, { onSignIn }) {
  const msg = el('p', { class: 'ld-msg small', role: 'status' });
  const cta = el('button', { type: 'button', class: 'btn btn-primary ld-cta', onclick: async () => {
    cta.disabled = true; msg.textContent = '';
    try { await onSignIn(); } catch { msg.textContent = 'Sign-in did not finish. Try again.'; } finally { cta.disabled = false; }
  } }, 'Sign in with Google');
  view.replaceChildren(el('div', { class: 'ld' },
    el('section', { class: 'ld-hero' },
      el('div', { class: 'ld-copy' },
        el('p', { class: 'ld-eyebrow' }, 'Smriti · private batch archive'),
        el('h1', { class: 'ld-title', tabindex: '-1' }, 'Every memory from the batch, in one private place.'),
        el('p', { class: 'ld-lede' }, 'The photos are hidden until you sign in. Use your registered school Google account to see them.'),
        el('div', { class: 'ld-actions' }, cta,
          el('span', { class: 'ld-lock' }, html(ICON.lock), 'Members only')),
        msg),
      el('div', { class: 'ld-visual' }, html(HERO))),
    el('section', { class: 'ld-cards', 'aria-label': 'What you get' },
      ...CARDS.map(([ic, h, p], i) => el('article', { class: 'ld-feature', style: { '--i': i } }, el('span', { class: 'ld-ico' }, html(ICON[ic])), el('h2', {}, h), el('p', {}, p)))),
    el('section', { class: 'ld-steps' },
      el('h2', {}, 'How to get in'),
      el('ol', {}, ...STEPS.map(([h, p]) => el('li', {}, el('b', {}, h), el('span', {}, p))))),
    el('p', { class: 'ld-foot small' }, 'Only rkmv<registration number>@gmail.com accounts on the class roster can sign in.')));
}
