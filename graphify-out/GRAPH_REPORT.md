# Graph Report - sorted_photos  (2026-11-06)

## Corpus Check
- 43 files · ~20,539 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: .css 3, (none) 1, .rules 1)

## Summary
- 340 nodes · 872 edges · 15 communities (11 shown, 4 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 30 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `10b788cc`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- faces.py
- app.js
- util.js
- el
- mount
- review_server.py
- api.test.mjs
- tests/package.json
- RKMV Gallery: design system
- js/auth.js
- functions/package.json
- README.md
- util.js

## God Nodes (most connected - your core abstractions)
1. `el()` - 54 edges
2. `mount()` - 20 edges
3. `mount()` - 20 edges
4. `mount()` - 18 edges
5. `label()` - 17 edges
6. `plural()` - 17 edges
7. `mount()` - 17 edges
8. `route()` - 16 edges
9. `errorResponse()` - 15 edges
10. `imgUrl()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `Hard rules` --references--> `el()`  [INFERRED]
  gallery/CONTRACT.md → gallery/js/util.js
- `Browser modules` --references--> `getToken()`  [INFERRED]
  gallery/CONTRACT.md → gallery/js/auth.js
- `Browser modules` --references--> `currentProfile()`  [INFERRED]
  gallery/CONTRACT.md → gallery/js/auth.js
- `Browser modules` --references--> `get()`  [INFERRED]
  gallery/CONTRACT.md → gallery/js/store.js
- `Browser modules` --references--> `loadMyVotes()`  [INFERRED]
  gallery/CONTRACT.md → gallery/js/votes.js

## Import Cycles
- None detected.

## Communities (15 total, 4 thin omitted)

### Community 0 - "faces.py"
Cohesion: 0.07
Nodes (25): fit(), main(), run(), process(), load(), lst(), main(), main() (+17 more)

### Community 1 - "app.js"
Cohesion: 0.12
Nodes (29): Browser modules, applyTheme(), checkAccess(), closeMenu(), ctx, firstUser(), init(), lazyViews (+21 more)

### Community 2 - "util.js"
Cohesion: 0.29
Nodes (12): icon, build(), close(), facesLayer(), fit(), onKey(), open(), order (+4 more)

### Community 3 - "el"
Cohesion: 0.31
Nodes (19): emit(), avatar(), el(), faceCrop(), mount(), render(), personRow(), mount() (+11 more)

### Community 4 - "mount"
Cohesion: 0.11
Nodes (47): empty(), fromQuery(), hashFor(), hay(), hayCache, isActive(), KEYS, link() (+39 more)

### Community 5 - "review_server.py"
Cohesion: 0.09
Nodes (18): Cloudflare, Collections, CONTRACT: crowd review build (every agent builds against this file; do not deviate, ask the lead), Endpoints (JSON; errors `{ok:false,error}` with 401/403/400/413), Facts from the existing code, Files per task (owners do not overlap), Firebase (Spark plan: Auth + Firestore only; NO Storage, NO Functions), Hard rules (+10 more)

### Community 6 - "api.test.mjs"
Cohesion: 0.13
Nodes (26): onRequestGet(), onRequestGet(), onRequestPost(), ALLOWED, onRequestGet(), onRequestGet(), onRequestPost(), readLimited() (+18 more)

### Community 7 - "tests/package.json"
Cohesion: 0.12
Nodes (11): devDependencies, firebase, @firebase/rules-unit-testing, firebase-tools, private, scripts, test, type (+3 more)

### Community 8 - "RKMV Gallery: design system"
Cohesion: 0.29
Nodes (6): Direction, RKMV Gallery: design system, Rules, Screens, Shell, Tokens

### Community 10 - "js/auth.js"
Cohesion: 0.14
Nodes (30): db, getProgress(), listAllVotes(), listOverrides(), listUsers(), saveOverride(), saveProgress(), setUserStatus() (+22 more)

### Community 14 - "util.js"
Cohesion: 0.17
Nodes (25): API, IMAGE_BASE, doLoad(), get(), index(), load(), store, subs (+17 more)

## Knowledge Gaps
- **1 isolated node(s):** `firebase-tools`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 79 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `CONTRACT: crowd review build (every agent builds against this file; do not deviate, ask the lead)` connect `review_server.py` to `app.js`?**
  _High betweenness centrality (0.282) - this node is a cross-community bridge._
- **What connects `firebase-tools` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `faces.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0701344243132671 - nodes in this community are weakly interconnected._
- **Why does `el()` connect `el` to `app.js`, `util.js`, `mount`, `review_server.py`, `js/auth.js`, `util.js`?**
  _High betweenness centrality (0.281) - this node is a cross-community bridge._
- **Should `app.js` be split into smaller, more focused modules?**
  _Cohesion score 0.12183908045977011 - nodes in this community are weakly interconnected._
- **Should `mount` be split into smaller, more focused modules?**
  _Cohesion score 0.10740203193033382 - nodes in this community are weakly interconnected._
- **Should `review_server.py` be split into smaller, more focused modules?**
  _Cohesion score 0.09359605911330049 - nodes in this community are weakly interconnected._