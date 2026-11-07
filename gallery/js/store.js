// Shared data store: fetches the JSON once, builds lookups, notifies listeners (open-question count).
import { getToken } from './auth.js';
import { loadMyVotes, loadProgress } from './votes.js';
import { API, DEMO } from './config.js';

export const store = {
  photos: [], people: [], graph: { nodes: [], edges: [] }, questions: [],
  byId: new Map(), peopleById: new Map(),
  scenes: [], sceneIds: [], tags: [], dates: new Map(),
  list: [], // current filtered list, set by the view under the lightbox
};

const subs = new Set();
export const onChange = (fn) => { subs.add(fn); return () => subs.delete(fn); };
export const emit = () => subs.forEach((f) => f());

const REVIEW = location.hostname === '127.0.0.1' || DEMO; // local review server and public demo: static data/ files, no sign-in

// Signed in: GET /api/data/<name> with the bearer token. Review server: data/<name>.json.
async function get(name, required) {
  try {
    const token = REVIEW ? null : await getToken();
    if (!token && !REVIEW) throw new Error('Sign in to load the gallery.');
    const r = await fetch(token ? `${API}/data/${name}` : `data/${name}.json`, {
      cache: 'no-cache', headers: token ? { authorization: `Bearer ${token}` } : {},
    });
    if (!r.ok) throw new Error(`${name}: ${r.status}`);
    return await r.json();
  } catch (e) {
    if (required) throw e;
    return null;
  }
}

function index() {
  const s = store;
  for (const p of s.photos) {
    p.people ||= []; p.activity ||= []; p.objects ||= []; p.mood ||= []; p.text ||= []; p.faces ||= [];
  }
  s.byId = new Map(s.photos.map((p) => [p.id, p]));
  s.peopleById = new Map(s.people.map((p) => [p.id, p]));
  s.questions.sort((a, b) => (b.priority || 0) - (a.priority || 0));

  const sc = new Map(), tg = new Map(), dt = new Map();
  for (const p of s.photos) {
    if (p.scene) {
      const e = sc.get(p.scene) || { id: p.scene, count: 0, first: p };
      e.count++; sc.set(p.scene, e);
    }
    for (const t of new Set([...p.activity, ...p.objects, ...p.mood])) tg.set(t, (tg.get(t) || 0) + 1);
    const d = p.date || 'none';
    dt.set(d, (dt.get(d) || 0) + 1);
  }
  s.scenes = [...sc.values()].sort((a, b) => b.count - a.count);
  const fromQs = s.questions.filter((q) => q.type === 'scene').flatMap((q) => q.all || []);
  s.sceneIds = [...new Set([...s.scenes.map((e) => e.id), ...fromQs])].sort();
  s.tags = [...tg].map(([t, n]) => ({ t, n })).sort((a, b) => b.n - a.n || a.t.localeCompare(b.t));
  s.dates = dt;
  s.list = s.photos;
}

let loading = null;
async function doLoad() {
  const [photos, people, graph, questions] = await Promise.all([
    get('photos', true), get('people', true), get('graph', false), get('questions', false),
  ]);
  let open = questions || [];
  if (!REVIEW && await getToken()) {
    // Hide questions this student already answered or that are settled (own votes + one progress read).
    const [mine, progress] = await Promise.all([
      loadMyVotes().catch(() => ({})), loadProgress().catch(() => ({})),
    ]);
    const settled = (progress && progress.settled) || {};
    open = open.filter((q) => !mine[q.id] && !settled[q.id]);
  }
  Object.assign(store, { photos, people, graph: graph || { nodes: [], edges: [] }, questions: open });
  index();
  emit();
  return store;
}

export function load(force = false) {
  if (!loading || force) loading = doLoad().catch((e) => { loading = null; throw e; });
  return loading;
}
