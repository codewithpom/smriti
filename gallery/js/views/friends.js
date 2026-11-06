import { el, label, plural } from '../util.js';
import { link } from '../filters.js';

const D3_URL = 'https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js';
let d3Promise = null;
function loadD3() {
  if (window.d3) return Promise.resolve(window.d3);
  d3Promise ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = D3_URL; s.defer = true;
    s.onload = () => resolve(window.d3);
    s.onerror = () => { d3Promise = null; reject(new Error('d3 failed to load')); };
    document.head.append(s);
  });
  return d3Promise;
}

const TIERS = ['close', 'classmates', 'seen together'];
const TIER_PILL = { close: 'pill-positive', classmates: '', 'seen together': 'pill-outline' };

export function mount(root, ctx) {
  const { store } = ctx;
  const { nodes = [], edges = [] } = store.graph;
  const simNodes = nodes.map((n) => ({
    id: n.id, label: n.label || (store.peopleById.get(n.id)?.name) || n.id, photos: n.photos || 0, community: n.community,
    r: Math.min(14, 3 + Math.sqrt(n.photos || 0) * 1.2),
  }));
  const nodeById = new Map(simNodes.map((n) => [n.id, n]));
  const goodEdges = edges.filter((e) => nodeById.has(e.a) && nodeById.has(e.b));
  const wmax = goodEdges.reduce((m, e) => Math.max(m, e.weight), 1e-9);
  const friendsOf = new Map();
  for (const e of goodEdges) {
    for (const [x, y] of [[e.a, e.b], [e.b, e.a]]) {
      if (!friendsOf.has(x)) friendsOf.set(x, []);
      friendsOf.get(x).push({ id: y, weight: e.weight, shared: e.shared, tier: e.tier });
    }
  }
  const labelled = new Set([...simNodes].sort((a, b) => b.photos - a.photos).slice(0, 12).map((n) => n.id));
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const state = { tiers: new Set(TIERS), hover: null, selected: null, list: false };
  let d3 = null, sim = null, dead = false, links = [], vis = [], adj = new Map(), t = { x: 0, y: 0, k: 1 };
  let listBtns = new Map(), ctx2d = null, size = { w: 0, h: 0 }, dpr = 1, col = null, zoom = null, ro = null;

  // default threshold keeps roughly the 500 strongest connections
  const sorted = goodEdges.map((e) => e.weight).sort((a, b) => b - a);
  const defMin = sorted.length > 500 ? Math.min(90, Math.ceil((sorted[500] / wmax) * 100)) : 0;

  // ---- controls
  const out = el('output', { class: 'num', text: defMin + '%' });
  const range = el('input', { type: 'range', min: 0, max: 100, step: 1, value: String(defMin), oninput: () => { out.textContent = range.value + '%'; rebuild(); } });
  const tierBtns = TIERS.map((tier) => el('button', {
    type: 'button', class: 'chip', 'aria-pressed': 'true',
    onclick: () => {
      if (state.tiers.has(tier)) state.tiers.delete(tier); else state.tiers.add(tier);
      tierBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(state.tiers.has(TIERS[i]))));
      rebuild();
    },
  }, label(tier)));
  const listBtn = el('button', { type: 'button', class: 'btn', 'aria-pressed': 'false', onclick: () => setList(!state.list) }, 'List view');
  const status = el('p', { class: 'muted small num', 'aria-live': 'polite' });
  const note = el('p', { class: 'fr-note muted', text: nodes.length ? 'Loading the graph...' : 'No connections yet.' });
  const canvas = el('canvas', { role: 'img', 'aria-label': 'Friendship graph. Use List view to browse with a keyboard or screen reader.' });
  const box = el('div', { class: 'graph-box' }, canvas, note);
  const listEl = el('ul', { class: 'plain rows fr-list', hidden: true });
  const panel = el('aside', { class: 'fr-panel', 'aria-live': 'polite', 'aria-label': 'Selected person' });

  root.append(
    el('div', { class: 'page-head' }, el('div', {}, el('h1', { text: 'Friends' }),
      el('p', { text: 'Closeness counts small group photos more than big assemblies.' }))),
    el('div', { class: 'fr-controls' },
      el('label', { class: 'fr-range' }, 'Minimum closeness', range, out),
      el('div', { class: 'chips', role: 'group', 'aria-label': 'Closeness tier' }, tierBtns),
      listBtn),
    status,
    el('div', { class: 'friends-wrap' }, el('div', {}, box, listEl), panel));

  function setList(on) {
    state.list = on;
    listBtn.setAttribute('aria-pressed', String(on));
    box.hidden = on;
    listEl.hidden = !on;
    if (!on) resize();
  }

  // ---- data for the current filters
  function rebuild() {
    const thr = (range.value / 100) * wmax;
    links = goodEdges.filter((e) => state.tiers.has(e.tier) && e.weight >= thr)
      .map((e) => ({ source: e.a, target: e.b, weight: e.weight, tier: e.tier, shared: e.shared }));
    adj = new Map();
    const strength = new Map();
    for (const l of links) {
      for (const [x, y] of [[l.source, l.target], [l.target, l.source]]) {
        if (!adj.has(x)) adj.set(x, new Set());
        adj.get(x).add(y);
        strength.set(x, (strength.get(x) || 0) + l.weight);
      }
    }
    vis = simNodes.filter((n) => adj.has(n.id) || n.id === state.selected);
    status.textContent = `${plural(vis.length, 'person')}, ${plural(links.length, 'connection')} shown`;
    renderList(strength);
    if (sim) {
      sim.nodes(vis);
      sim.force('link').links(links);
      if (reduceMotion) { sim.alpha(1); sim.stop(); sim.tick(250); draw(); } else sim.alpha(0.6).restart();
    }
  }

  function renderList(strength) {
    const byStrength = [...vis].sort((a, b) => (strength.get(b.id) || 0) - (strength.get(a.id) || 0));
    listBtns = new Map();
    listEl.replaceChildren(...byStrength.map((n) => {
      const b = el('button', { type: 'button', class: 'row-btn', 'aria-pressed': String(state.selected === n.id), onclick: () => select(n.id) },
        el('span', { class: 'row-main' }, el('span', { class: 'row-name', text: n.label }), el('span', { class: 'muted small', text: plural(n.photos, 'photo') })));
      listBtns.set(n.id, b);
      return el('li', {}, b);
    }));
  }

  function select(id) {
    state.selected = id;
    renderPanel();
    if (id && !vis.some((n) => n.id === id)) { rebuild(); return; }
    listBtns.forEach((b, k) => b.setAttribute('aria-pressed', String(k === id)));
    draw();
  }

  function renderPanel() {
    const n = state.selected && nodeById.get(state.selected);
    if (!n) { panel.replaceChildren(el('p', { class: 'muted', text: 'Select a person to see their closest friends.' })); return; }
    const top = (friendsOf.get(n.id) || []).sort((a, b) => b.weight - a.weight).slice(0, 8);
    panel.replaceChildren(
      el('h2', { text: n.label }),
      el('p', { class: 'muted small num', text: plural(n.photos, 'photo') }),
      top.length ? el('ul', { class: 'plain' }, top.map((f) => {
        const o = nodeById.get(f.id);
        return el('li', {},
          el('button', { type: 'button', class: 'row-btn', onclick: () => select(f.id) },
            el('span', { class: 'row-main' }, el('span', { class: 'row-name', text: o.label }), el('span', { class: 'muted small num', text: `${plural(f.shared || 0, 'photo')} together` }))),
          el('span', { class: 'pill ' + (TIER_PILL[f.tier] || ''), text: label(f.tier || '') }));
      })) : el('p', { class: 'muted small', text: 'No connections found.' }),
      el('div', { class: 'fr-panel-links' },
        el('a', { class: 'btn btn-primary', href: link({ person: [n.id] }), text: 'See photos' }),
        el('a', { class: 'btn', href: '#/person/' + encodeURIComponent(n.id), text: 'Open profile' })));
  }

  // ---- canvas drawing
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    col = { c: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v('--c' + i)), text: v('--text'), surface: v('--surface'), muted: v('--muted'), accent: v('--accent') };
  }

  function draw() {
    if (!ctx2d || dead || !col) return;
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx2d.clearRect(0, 0, size.w, size.h);
    ctx2d.translate(t.x, t.y);
    ctx2d.scale(t.k, t.k);
    const focus = state.hover || state.selected;
    const nb = focus ? adj.get(focus) : null;
    ctx2d.strokeStyle = col.muted;
    for (const l of links) {
      const wn = l.weight / wmax;
      const on = !focus || l.source.id === focus || l.target.id === focus;
      ctx2d.globalAlpha = on ? 0.15 + 0.6 * wn : 0.03;
      ctx2d.lineWidth = 0.5 + 2.5 * wn;
      ctx2d.beginPath();
      ctx2d.moveTo(l.source.x, l.source.y);
      ctx2d.lineTo(l.target.x, l.target.y);
      ctx2d.stroke();
    }
    for (const n of vis) {
      const on = !focus || n.id === focus || (nb && nb.has(n.id));
      ctx2d.globalAlpha = on ? 1 : 0.15;
      ctx2d.fillStyle = col.c[(Number(n.community) || 0) % 8] || col.c[7];
      ctx2d.beginPath();
      ctx2d.arc(n.x, n.y, n.r, 0, 6.2832);
      ctx2d.fill();
      if (n.id === state.selected) { ctx2d.lineWidth = 2.5 / t.k; ctx2d.strokeStyle = col.accent; ctx2d.stroke(); }
    }
    ctx2d.globalAlpha = 1;
    ctx2d.font = `500 ${12 / t.k}px "DM Sans", system-ui, sans-serif`;
    ctx2d.textAlign = 'center';
    ctx2d.textBaseline = 'top';
    ctx2d.lineJoin = 'round';
    ctx2d.lineWidth = 3 / t.k;
    for (const n of vis) {
      const on = !focus || n.id === focus || (nb && nb.has(n.id));
      if (!on || (!focus && !labelled.has(n.id))) continue;
      ctx2d.strokeStyle = col.surface;
      ctx2d.strokeText(n.label, n.x, n.y + n.r + 2 / t.k);
      ctx2d.fillStyle = col.text;
      ctx2d.fillText(n.label, n.x, n.y + n.r + 2 / t.k);
    }
  }

  function resize() {
    if (!d3 || dead || box.hidden) return;
    const r = box.getBoundingClientRect();
    if (!r.width) return;
    dpr = window.devicePixelRatio || 1;
    size = { w: r.width, h: r.height };
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    draw();
  }

  function hit(e) {
    const r = canvas.getBoundingClientRect();
    const [x, y] = [(e.clientX - r.left - t.x) / t.k, (e.clientY - r.top - t.y) / t.k];
    return sim.find(x, y, 16 / t.k);
  }

  function start(lib) {
    d3 = lib;
    note.remove();
    ctx2d = canvas.getContext('2d');
    readColors();
    sim = d3.forceSimulation()
      .force('link', d3.forceLink().id((d) => d.id)
        .distance((l) => Math.min(180, 20 + 20 / Math.max(l.weight / wmax, 0.1)))
        .strength((l) => 0.15 + 0.5 * (l.weight / wmax)))
      .force('charge', d3.forceManyBody().strength(-140).distanceMax(400))
      .force('center', d3.forceCenter(0, 0))
      .force('collide', d3.forceCollide().radius((d) => d.r + 2))
      .on('tick', draw);
    zoom = d3.zoom().scaleExtent([0.3, 8]).on('zoom', (e) => { t = e.transform; draw(); });
    d3.select(canvas).call(zoom);
    canvas.addEventListener('pointermove', (e) => {
      if (e.pointerType === 'touch') return;
      const n = hit(e);
      const id = n ? n.id : null;
      canvas.style.cursor = n ? 'pointer' : '';
      if (id !== state.hover) { state.hover = id; draw(); }
    });
    canvas.addEventListener('pointerleave', () => { if (state.hover) { state.hover = null; draw(); } });
    canvas.addEventListener('click', (e) => { const n = hit(e); select(n ? n.id : null); });
    ro = new ResizeObserver(resize);
    ro.observe(box);
    window.addEventListener('themechange', onTheme);
    rebuild();
    const r = box.getBoundingClientRect();
    d3.select(canvas).call(zoom.transform, d3.zoomIdentity.translate(r.width / 2, r.height / 2));
    resize();
  }
  function onTheme() { readColors(); draw(); }

  renderPanel();
  rebuild();
  if (nodes.length) {
    loadD3().then((lib) => { if (!dead) start(lib); }).catch(() => {
      if (dead) return;
      note.textContent = 'The graph library could not be loaded. The list view still works.';
      setList(true);
    });
  } else setList(true);

  return {
    title: 'Friends',
    unmount() { dead = true; sim?.stop(); ro?.disconnect(); window.removeEventListener('themechange', onTheme); },
  };
}
