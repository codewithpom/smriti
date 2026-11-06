// Shared data store: fetches the JSON once, builds lookups, notifies listeners (open-question count).
export const store = {
  photos: [], people: [], graph: { nodes: [], edges: [] }, questions: [],
  byId: new Map(), peopleById: new Map(),
  scenes: [], sceneIds: [], tags: [], dates: new Map(),
  list: [], // current filtered list, set by the view under the lightbox
};

const subs = new Set();
export const onChange = (fn) => { subs.add(fn); return () => subs.delete(fn); };
export const emit = () => subs.forEach((f) => f());

async function get(name, required) {
  try {
    const r = await fetch(`data/${name}.json`, { cache: 'no-cache' });
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
  Object.assign(store, { photos, people, graph: graph || { nodes: [], edges: [] }, questions: questions || [] });
  index();
  emit();
  return store;
}

export function load(force = false) {
  if (!loading || force) loading = doLoad().catch((e) => { loading = null; throw e; });
  return loading;
}
