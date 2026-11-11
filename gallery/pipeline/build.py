"""Merge catalog + tags + faces + corrections into the files the site reads, build the friendship graph and the question queue.

  python pipeline/build.py        (run after `faces.py match`; the review server runs both after answers)

Writes data/{photos,people,graph,questions,clusters}.json
"""
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

DATA = Path(__file__).resolve().parent.parent / "data"
SCENES = ["campus-courtyard-exterior", "classroom", "corridor-stairs", "dining-hall", "exhibition-fair", "garden-fields",
          "hostel", "indoor-other", "library", "office-storeroom", "outdoor-other", "outing-trip", "playground-sports",
          "prayer-temple", "screenshots-collage", "stage-auditorium-hall", "unclear-selfie"]
VAGUE = {"indoor-other", "outdoor-other", "unclear-selfie", "screenshots-collage"}
GOOD = ("confirmed", "high")   # statuses that count as "this person is in the photo"


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def lst(v):
    """Haiku sometimes returns a bare string where a list is expected."""
    return [v] if isinstance(v, str) and v else (v if isinstance(v, list) else [])


def main():
    catalog = load("catalog.json", [])
    faces = load("faces.json", {})
    meta = load("people_meta.json", {})
    corr = load("corrections.json", {})
    tags = {}
    for p in sorted(DATA.glob("tags_shard_*.json")):
        tags.update(json.loads(p.read_text(encoding="utf-8")))
    scene_fix = corr.get("scenes", {})

    # ---- photos
    photos = []
    published = [e for e in catalog if not e.get("pending") and not e.get("dup_of")]
    pending = [e for e in catalog if e.get("pending")]
    for e in published:
        t = tags.get(e["id"], {})
        fs = faces.get(e["id"], [])
        named = sorted({f["person"] for f in fs if f["person"] and f["status"] in GOOD})
        photo = {
            "id": e["id"], "scene": scene_fix.get(e["id"], e["scene"]), "folder_scene": e["scene"],
            "scene_confirmed": e["id"] in scene_fix, "date": e["date"], "w": e["width"], "h": e["height"],
            "color": e.get("color", "#888888"), "thumb": f"img/thumb/{e['id']}.webp", "web": f"img/web/{e['id']}.webp",
            "tw": e.get("thumb_w"), "th": e.get("thumb_h"),
            "activity": lst(t.get("activity")), "objects": lst(t.get("objects")), "mood": lst(t.get("mood")),
            "crowd": t.get("people"), "text": lst(t.get("text")), "alt": t.get("alt", ""),
            "faces": [{k: f[k] for k in ("i", "bbox", "person", "status", "score")} for f in fs],
            "people": named, "n_faces": len(fs),
        }
        if e.get("ph"):
            photo["ph"] = e["ph"]
        photos.append(photo)
    by_id = {p["id"]: p for p in photos}

    # ---- graph: a pair's weight is the sum over shared photos of 1/(faces-1), so a 3-person candid beats a 60-person assembly
    pair_w, pair_n, person_n = defaultdict(float), defaultdict(int), defaultdict(int)
    pair_small = defaultdict(int)
    for p in photos:
        ppl = p["people"]
        for a in ppl:
            person_n[a] += 1
        n = max(p["n_faces"], len(ppl))
        if len(ppl) < 2 or n < 2:
            continue
        w = 1 / (n - 1)
        for i, a in enumerate(ppl):
            for b in ppl[i + 1:]:
                pair_w[(a, b)] += w; pair_n[(a, b)] += 1
                if n <= 8:
                    pair_small[(a, b)] += 1
    G = nx.Graph()
    for (a, b), w in pair_w.items():
        if pair_n[(a, b)] >= 2 or w >= 0.3:
            G.add_edge(a, b, weight=round(w, 3), shared=pair_n[(a, b)], small=pair_small[(a, b)])
    comm = {}
    if G.number_of_edges():
        for ci, c in enumerate(nx.community.louvain_communities(G, weight="weight", seed=7)):
            for n in c:
                comm[n] = ci
    edges = []
    for a, b, d in G.edges(data=True):
        sa, sb = meta.get(a, {}), meta.get(b, {})
        same_class = sa.get("kind") == sb.get("kind") == "student" and sa.get("section") and sa.get("section") == sb.get("section")
        tier = "close" if d["small"] >= 2 else ("classmates" if same_class else "seen together")
        edges.append({"a": a, "b": b, "weight": d["weight"], "shared": d["shared"], "tier": tier})
    friends = defaultdict(list)
    for e in edges:
        friends[e["a"]].append({"id": e["b"], "weight": e["weight"], "shared": e["shared"], "tier": e["tier"]})
        friends[e["b"]].append({"id": e["a"], "weight": e["weight"], "shared": e["shared"], "tier": e["tier"]})
    people = []
    for pid, m in meta.items():
        top = sorted(friends.get(pid, []), key=lambda f: -f["weight"])[:8]
        people.append({**m, "photos": person_n.get(pid, 0), "avatar": f"img/people/{pid}.webp",
                       "community": comm.get(pid), "friends": top})
    people.sort(key=lambda p: (-p["photos"], p["name"]))
    nodes = [{"id": p["id"], "label": p["name"], "kind": p["kind"], "photos": p["photos"], "community": p["community"]}
             for p in people if p["photos"] > 0 and p["id"] in G]

    # ---- unknown clusters (one answer names the whole cluster)
    clusters = defaultdict(list)
    for pid, fs in faces.items():
        if pid not in by_id:
            continue
        for f in fs:
            if f.get("cluster") and not f["person"]:
                clusters[f["cluster"]].append((pid, f))
    cl_out, questions = [], []
    for cid, members in clusters.items():
        members.sort(key=lambda m: -m[1]["bbox"][2] * m[1]["bbox"][3])
        votes = defaultdict(float)
        for _, f in members:
            for pid, s in f["cands"]:
                votes[pid] += s
        cands = [pid for pid, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:3]]
        sample = [{"photo": ph, "i": f["i"], "bbox": f["bbox"]} for ph, f in members[:8]]
        cl_out.append({"id": cid, "size": len(members), "cands": cands, "sample": sample})
        questions.append({"id": f"c{cid}", "type": "cluster", "priority": round(min(1.0, 0.5 + len(members) / 40), 3),
                          "cluster": cid, "size": len(members), "cands": cands, "sample": sample,
                          "faces": [f"{ph}:{f['i']}" for ph, f in members]})

    # ---- face questions: confident-ish candidates not covered by a cluster question
    for pid, fs in faces.items():
        if pid not in by_id:
            continue
        for f in fs:
            if f["status"] == "candidate" and not f.get("cluster") and f["cands"]:
                questions.append({"id": f"f{pid}:{f['i']}", "type": "face", "priority": round(0.3 + f["cands"][0][1] / 2, 3),
                                  "photo": pid, "i": f["i"], "bbox": f["bbox"], "cands": [c[0] for c in f["cands"]]})

    # ---- scene questions
    for p in photos:
        t = tags.get(p["id"])
        if p["scene_confirmed"]:
            continue
        reason, prio = None, 0
        if t and t.get("scene") != p["folder_scene"] and t.get("confidence", 0) >= 0.5:
            reason, prio = "disagree", 0.9
        elif p["folder_scene"] in VAGUE:
            reason, prio = "vague", 0.6
        elif t and t.get("confidence", 1) < 0.7:
            reason, prio = "low", 0.5
        if reason:
            opts = [p["folder_scene"]] + [s for s in (t.get("scene") if t else None, t.get("alt_scene") if t else None) if s and s != p["folder_scene"]]
            questions.append({"id": f"s{p['id']}", "type": "scene", "priority": prio, "photo": p["id"], "reason": reason,
                              "options": list(dict.fromkeys(opts))[:3], "all": SCENES})
    questions = [q for q in questions if q["id"] not in set(corr.get("skipped", []))]
    questions.sort(key=lambda q: -q["priority"])

    pending_out = []
    for e in pending:
        fs = faces.get(e["id"], [])
        at = e.get("date")
        upload_key = e.get("upload_key", "")
        try:
            millis = int(upload_key.rsplit("/", 1)[1].split("-", 1)[0])
            at = datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()
        except (IndexError, ValueError, OSError):
            pass
        pending_out.append({"photo": e["id"], "thumb": f"img/thumb/{e['id']}.webp", "web": f"img/web/{e['id']}.webp",
                            "uploader": e.get("uploader"), "at": at,
                            "faces": [{"person": f.get("person"), "status": f.get("status")} for f in fs],
                            **({"dupOf": e["dup_of"]} if e.get("dup_of") else {})})
    (DATA / "pending.json").write_text(json.dumps({"items": pending_out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    out = {"photos.json": photos, "people.json": people, "graph.json": {"nodes": nodes, "edges": edges},
           "questions.json": questions, "clusters.json": cl_out}
    for name, obj in out.items():
        (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_named = sum(1 for p in photos if p["people"])
    print(f"photos {len(photos)} (with named people {n_named}), people with photos {sum(1 for p in people if p['photos'])}, "
          f"edges {len(edges)}, communities {len(set(comm.values()))}, questions {len(questions)}")
    # sanity: graph has no self-edges and every edge endpoint is a known person
    assert all(e["a"] != e["b"] and e["a"] in meta and e["b"] in meta for e in edges)


if __name__ == "__main__":
    main()
