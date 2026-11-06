# Graph Report - sorted_photos  (2026-11-06)

## Corpus Check
- 704 files · ~21,410,222 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: .log 7, .css 2, .npy 2)

## Summary
- 189 nodes · 507 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 20 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- faces.py
- app.js
- util.js
- el
- mount
- review_server.py
- lightbox.js
- mount
- RKMV Gallery: design system

## God Nodes (most connected - your core abstractions)
1. `el()` - 41 edges
2. `mount()` - 20 edges
3. `mount()` - 18 edges
4. `label()` - 17 edges
5. `plural()` - 17 edges
6. `mount()` - 17 edges
7. `imgUrl()` - 14 edges
8. `link()` - 13 edges
9. `avatar()` - 13 edges
10. `route()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `showError()` --calls--> `el()`  [EXTRACTED]
  gallery/js/app.js → gallery/js/util.js
- `route()` --calls--> `close()`  [EXTRACTED]
  gallery/js/app.js → gallery/js/views/lightbox.js
- `route()` --calls--> `isOpen()`  [EXTRACTED]
  gallery/js/app.js → gallery/js/views/lightbox.js
- `route()` --calls--> `open()`  [EXTRACTED]
  gallery/js/app.js → gallery/js/views/lightbox.js
- `mount()` --calls--> `link()`  [EXTRACTED]
  gallery/js/views/friends.js → gallery/js/filters.js

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 0 - "faces.py"
Cohesion: 0.10
Nodes (18): fit(), main(), run(), process(), load(), lst(), main(), main() (+10 more)

### Community 1 - "app.js"
Cohesion: 0.12
Nodes (28): applyTheme(), closeMenu(), ctx, init(), menu, menuBtn, mountView(), navFor (+20 more)

### Community 2 - "util.js"
Cohesion: 0.17
Nodes (22): API, IMAGE_BASE, link(), ar(), renderGrid(), layout(), tile(), add() (+14 more)

### Community 3 - "el"
Cohesion: 0.30
Nodes (20): emit(), avatar(), el(), faceCrop(), postJSON(), mount(), render(), personRow() (+12 more)

### Community 4 - "mount"
Cohesion: 0.26
Nodes (17): empty(), fromQuery(), hashFor(), hay(), hayCache, isActive(), KEYS, makePredicate() (+9 more)

### Community 5 - "review_server.py"
Cohesion: 0.20
Nodes (6): answer(), H, jload(), jsave(), rebuild(), undo()

### Community 6 - "lightbox.js"
Cohesion: 0.29
Nodes (12): icon, build(), close(), facesLayer(), fit(), onKey(), open(), order (+4 more)

### Community 7 - "mount"
Cohesion: 0.47
Nodes (11): mount(), draw(), hit(), onTheme(), readColors(), rebuild(), renderList(), resize() (+3 more)

### Community 8 - "RKMV Gallery: design system"
Cohesion: 0.29
Nodes (6): Direction, RKMV Gallery: design system, Rules, Screens, Shell, Tokens

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `el()` connect `el` to `app.js`, `util.js`, `mount`, `lightbox.js`, `mount`?**
  _High betweenness centrality (0.137) - this node is a cross-community bridge._
- **Should `faces.py` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._
- **Why does `mount()` connect `mount` to `util.js`, `el`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Should `app.js` be split into smaller, more focused modules?**
  _Cohesion score 0.11612903225806452 - nodes in this community are weakly interconnected._
- **Why does `label()` connect `util.js` to `app.js`, `el`, `mount`, `lightbox.js`, `mount`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._