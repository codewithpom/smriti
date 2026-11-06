// Filter state <-> URL query (inside the hash) + predicate.
import { label } from './util.js';

export const KEYS = ['scene', 'person', 'tag', 'date'];
export const empty = () => ({ q: '', scene: [], person: [], tag: [], date: [], all: false });

export function fromQuery(sp) {
  const s = empty();
  s.q = sp.get('q') || '';
  for (const k of KEYS) s[k] = sp.getAll(k).filter(Boolean);
  s.all = sp.get('all') === '1';
  return s;
}

export function toQuery(s) {
  const sp = new URLSearchParams();
  if (s.q.trim()) sp.set('q', s.q.trim());
  for (const k of KEYS) for (const v of s[k]) sp.append(k, v);
  if (s.all && s.person.length > 1) sp.set('all', '1');
  return sp.toString();
}

export const hashFor = (s) => { const q = toQuery(s); return '#/' + (q ? '?' + q : ''); };
// Shareable link to the gallery with some filters: link({scene:['classroom']})
export const link = (patch) => hashFor({ ...empty(), ...patch });
export const isActive = (s) => !!(s.q.trim() || KEYS.some((k) => s[k].length));

const hayCache = new Map();
function hay(p, store) {
  let h = hayCache.get(p.id);
  if (h == null) {
    const names = p.people.map((id) => store.peopleById.get(id)?.name || '');
    h = [label(p.scene), ...p.activity, ...p.objects, ...p.mood, ...p.text, ...names]
      .join(' ').replace(/-/g, ' ').toLowerCase();
    hayCache.set(p.id, h);
  }
  return h;
}

// OR inside a group, AND across groups; person group is AND when s.all.
export function makePredicate(s, store) {
  const toks = s.q.toLowerCase().split(/\s+/).filter(Boolean);
  const sc = new Set(s.scene), tg = new Set(s.tag);
  return (p) => {
    if (sc.size && !sc.has(p.scene)) return false;
    if (s.person.length) {
      const ok = s.all ? s.person.every((x) => p.people.includes(x)) : s.person.some((x) => p.people.includes(x));
      if (!ok) return false;
    }
    if (tg.size && ![...p.activity, ...p.objects, ...p.mood].some((t) => tg.has(t))) return false;
    if (s.date.length && !s.date.some((d) => (d === 'none' ? !p.date : p.date && p.date.startsWith(d)))) return false;
    if (toks.length) { const h = hay(p, store); if (!toks.every((t) => h.includes(t))) return false; }
    return true;
  };
}
