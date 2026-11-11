"""Face pipeline.

  python pipeline/faces.py extract   # slow: detect + embed every face (InsightFace buffalo_l), writes embeddings
  python pipeline/faces.py match     # fast: match to the student/staff list, apply corrections.json, cluster unknowns

Names come only from D:\\hello (portraits + name/reg/section). Nothing else from that folder is used.
"""
import argparse
import gc
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

import auto_people
from face_quality import quality, quality_without_crop

GALLERY = Path(__file__).resolve().parent.parent
DATA = GALLERY / "data"
SRC = Path(os.environ.get("RKMV_SOURCE", GALLERY.parent / "sorted"))
HELLO = Path(os.environ.get("RKMV_PEOPLE", GALLERY.parent / "people"))  # folder with identities.json and the student portraits

MAX_SIDE = 2560          # detection resolution cap; small faces in group shots need pixels
MIN_DET = 0.60           # detector confidence floor
MIN_FACE_PX = 28         # faces smaller than this (in the 2560-capped image) are not matched

T_HIGH = 0.42            # cosine similarity for a confident match (portrait vs candid is a hard pair)
T_MARGIN = 0.06          # best must beat the second-best person by this
T_CAND = 0.28            # below this a face is unknown; between is a question candidate
CLUSTER_DIST = 0.55      # cosine distance for grouping unknown faces
MIN_CLUSTER = 2          # smaller groups stay loose faces (Immich uses 3; the batch is small so 2 surfaces more people)
Q_CORE = 0.35
LOWQ_EXTRA = 0.08
ATTACH_DIST = 0.45
CLUSTER_METHOD = os.environ.get("FACE_CLUSTER", "hdbscan")
ADA_MODEL = Path(__file__).resolve().parent / ".models" / "adaface_ir101.onnx"
MODEL_TAG = "r50f+ada" if os.environ.get("FACE_MODEL") == "r50f+ada" and ADA_MODEL.exists() else "r50f"


def atomic_write(path, text):
    """Write then rename, so an interrupted run never leaves truncated JSON that later runs trust."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def staff_aliases(people_raw):
    """Map duplicate staff ids to the role-bearing, then lowest-id entry."""
    groups = {}
    for person in people_raw:
        name = re.sub(r"\bshri\b", "sri", re.sub(r"\([^)]*\)", "", person["name"].lower()))
        key = "".join(c for c in name if c.isalpha())
        groups.setdefault(key, []).append(person)
    aliases = {}
    for group in groups.values():
        canonical = min(group, key=lambda p: (not bool(p.get("role")), p["id"]))
        aliases.update({p["id"]: canonical["id"] for p in group if p["id"] != canonical["id"]})
    return aliases


def merge_aliases(auto, manual):
    """Combine the name rule with hand-written merges and follow chains (a -> b -> c becomes a -> c)."""
    out = {**auto, **manual}
    for dup in out:
        seen = {dup}
        while out[dup] in out and out[dup] not in seen:
            seen.add(out[dup]); out[dup] = out[out[dup]]
    return {d: c for d, c in out.items() if d != c}


def load_aliases(ids=None):
    ids = ids or json.loads((HELLO / "identities.json").read_text(encoding="utf-8"))
    manual = DATA / "manual_aliases.json"   # {"STAFF_195": "STAFF_082"}: merges the name rule cannot see, e.g. spelling variants
    return merge_aliases(staff_aliases(ids["staff"]), json.loads(manual.read_text(encoding="utf-8")) if manual.exists() else {})


def load_people():
    ids = json.loads((HELLO / "identities.json").read_text(encoding="utf-8"))
    people = {}
    for s in ids["students"]:
        pid = f"S{s['reg_no']}"
        people[pid] = {"id": pid, "kind": "student", "name": s["name"], "reg_no": s["reg_no"], "batch": "MB27",
                       "section": s.get("section"), "portrait_src": HELLO / "reference_faces" / "students" / f"{s['reg_no']}.jpg"}
    for t in ids["staff"]:
        people[t["id"]] = {"id": t["id"], "kind": "staff", "name": t["name"], "role": t.get("role"),
                           "portrait_src": next((HELLO / "reference_faces" / "staff").glob(t["id"] + "_*.jpg"), None)}
    extra_staff = DATA / "extra_staff.json"   # staff added from the naming sheet: no reg number, no portrait
    if extra_staff.exists():
        for t in json.loads(extra_staff.read_text(encoding="utf-8")):
            people.setdefault(t["id"], {"id": t["id"], "kind": "staff", "name": t["name"], "role": t.get("role") or "", "portrait_src": None})
    noreg = DATA / "extra_students_noreg.json"   # students added from the naming sheet before their reg number is known
    if noreg.exists():
        for s in json.loads(noreg.read_text(encoding="utf-8")):
            people.setdefault(s["id"], {"id": s["id"], "kind": "student", "name": s["name"], "reg_no": "", "batch": s.get("batch") or "MB28",
                                        "section": None, "portrait_src": None})
    extra = DATA / "extra_students.json"   # students fetched later by reg number (name/section only, no marks)
    if extra.exists():
        for s in json.loads(extra.read_text(encoding="utf-8")):
            pid = f"S{s['reg_no']}"
            own = DATA / "extra_refs" / f"{s['reg_no']}.jpg"
            old = people.get(pid, {})   # no name yet (CSV comes later): show the reg number
            people[pid] = {"id": pid, "kind": "student", "name": s.get("name") or old.get("name") or f"Reg {s['reg_no']}",
                           "reg_no": s["reg_no"], "batch": s.get("batch") or old.get("batch") or "MB27", "section": s.get("section"),
                           "portrait_src": own if own.exists() else people.get(pid, {}).get("portrait_src")}
    for pid in load_aliases(ids):   # duplicates and hand-linked people disappear last, whichever file they came from
        people.pop(pid, None)
    return people


def load_rgb(path, cap=MAX_SIDE):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        s = min(1.0, cap / max(w, h))
        if s < 1.0:
            im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
        return np.asarray(im)[:, :, ::-1].copy()  # BGR for insightface


def cv2_error():
    import cv2
    return cv2.error


def get_app():
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"], allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=-1, det_size=(1024, 1024))
    return app


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def get_ada_session():
    if MODEL_TAG != "r50f+ada":
        return None
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    return ort.InferenceSession(str(ADA_MODEL), sess_options=opts, providers=["CPUExecutionProvider"])


def embed_aligned(app, aligned, ada=None):
    """Unit-normalise each flip, then their mean; optionally fuse two unit models."""
    crops = [aligned, np.ascontiguousarray(aligned[:, ::-1])]
    emb = unit(unit(app.models["recognition"].get_feat(crops)).mean(axis=0))
    if ada is not None:
        rgb = np.stack([c[:, :, ::-1] for c in crops]).astype(np.float32)
        x = ((rgb / 255 - 0.5) / 0.5).transpose(0, 3, 1, 2)
        features = ada.run(None, {ada.get_inputs()[0].name: np.ascontiguousarray(x)})[0]
        emb = np.concatenate([emb, unit(unit(features).mean(axis=0))]) / np.sqrt(2)
    return emb


def aligned_face(img, face):
    from insightface.utils.face_align import norm_crop
    return norm_crop(img, face.kps, image_size=112)


def _model_loader():
    app = None
    def load():
        nonlocal app
        if app is None:
            app = get_app()
        return app
    return load


def _portrait_views(img, retry=False):
    """Images to try detection on. Tight head-and-shoulders crops defeat SCRFD (no context around the
    face), so a copy with a 50% black border follows the original; fixrefs also tries rescaled copies."""
    import cv2
    h, w = img.shape[:2]
    yield img
    yield cv2.copyMakeBorder(img, h // 2, h // 2, w // 2, w // 2, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    if retry:
        for scale in (1.6, 0.6):
            yield cv2.resize(img, None, fx=scale, fy=scale)


def _references(people, load_app, ada, retry=False):
    """Reuse unchanged portraits; fingerprints also invalidate a different model."""
    ids_path, src_path = DATA / "ref_ids.json", DATA / "ref_src.json"
    old_src = json.loads(src_path.read_text(encoding="utf-8")) if src_path.exists() else {}
    old = {}
    if ids_path.exists() and (DATA / "ref_emb.npy").exists():
        old = dict(zip(json.loads(ids_path.read_text(encoding="utf-8")), np.load(DATA / "ref_emb.npy", allow_pickle=False)))
    ids, embeddings, sources = [], [], {}
    missing = changed = 0
    dim = 1024 if MODEL_TAG == "r50f+ada" else 512
    for pid, person in people.items():
        src = person["portrait_src"]
        if not src or not Path(src).exists():
            # An incremental runner may not have old portraits, either.
            if pid in old and pid in old_src and len(old[pid]) == dim:
                ids.append(pid); embeddings.append(old[pid]); sources[pid] = old_src[pid]
            else:
                missing += 1
            continue
        fingerprint = hashlib.sha1(Path(src).read_bytes() + MODEL_TAG.encode()).hexdigest()
        if pid in old and old_src.get(pid) == fingerprint and len(old[pid]) == dim:
            embedding = old[pid]
        else:
            app = load_app()
            if retry:
                app.det_model.det_thresh = 0.3
            embedding = None
            for img in _portrait_views(load_rgb(src, 1280), retry):
                found = app.get(img)
                if found:
                    face = max(found, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    embedding = embed_aligned(app, aligned_face(img, face), ada)
                    changed += 1
                    break
            if embedding is None and pid in old and len(old[pid]) == dim:
                embedding = old[pid]   # detection failed this time; keep the embedding an earlier run (or fixrefs) found
            if embedding is None:
                missing += 1
                continue
        ids.append(pid); embeddings.append(embedding); sources[pid] = fingerprint
    np.save(DATA / "ref_emb.npy", np.asarray(embeddings, dtype=np.float32).reshape(-1, dim))
    atomic_write(ids_path, json.dumps(ids))
    atomic_write(src_path, json.dumps(sources))
    if retry:
        atomic_write(DATA / "ref_failed.json", json.dumps(sorted(set(people) - set(ids))))
    return len(ids), missing, changed


def _people_outputs(people):
    out = GALLERY / "img" / "people"; out.mkdir(parents=True, exist_ok=True)
    for pid, person in people.items():
        src = person["portrait_src"]
        if src and Path(src).exists() and not (out / f"{pid}.webp").exists():
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB"); im.thumbnail((240, 240))
                im.save(out / f"{pid}.webp", quality=80)
    atomic_write(DATA / "people_meta.json", json.dumps(
        {pid: {k: v for k, v in p.items() if k != "portrait_src"} for pid, p in people.items()}, ensure_ascii=False))


def extract(reembed=False):
    load_app = _model_loader()
    ada = get_ada_session()
    people = load_people()
    ref_count, ref_missing, _ = _references(people, load_app, ada)
    _people_outputs(people)
    catalog = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    cache = DATA / "fx" / MODEL_TAG; cache.mkdir(parents=True, exist_ok=True)
    rows, embs = [], []
    missing = oom = 0
    dim = 1024 if MODEL_TAG == "r50f+ada" else 512
    for n, e in enumerate(catalog, 1):
        cf = cache / f"{e['id']}.json"
        legacy = DATA / "fx" / f"{e['id']}.json"
        # reembed ignores the legacy cache, except when the source photo is not on this machine: keep its faces then
        if not cf.exists() and legacy.exists() and (not reembed or not (SRC / e["file"]).exists()):
            cf = legacy
        if not cf.exists():
            if not (SRC / e["file"]).exists():
                missing += 1
                continue
            for cap in (MAX_SIDE, 1600, 1024):          # low-RAM machine: retry smaller on OOM
                try:
                    img = load_rgb(SRC / e["file"], cap)
                    H, W = img.shape[:2]
                    app = load_app()
                    faces = app.get(img)
                    cached = []
                    for f in faces:
                        aligned = aligned_face(img, f)
                        px = min(f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1])
                        cached.append({"b": np.asarray(f.bbox).tolist(), "k": np.asarray(f.kps).tolist(),
                                       "w": W, "h": H, "d": float(f.det_score),
                                       "q": quality(f.det_score, px, f.kps, aligned),
                                       "v": embed_aligned(app, aligned, ada).tolist()})
                    break
                except (MemoryError, cv2_error()):
                    gc.collect(); faces = None
            if faces is None:
                oom += 1
                continue
            atomic_write(cf, json.dumps(cached))
        found = []
        for f in json.loads(cf.read_text(encoding="utf-8")):
            x1, y1, x2, y2 = f["b"]; W, H = f["w"], f["h"]
            if f["d"] < MIN_DET and not f.get("embedding_missing"):
                continue
            px = int(min(x2 - x1, y2 - y1))
            found.append({"bbox": [round(x1 / W, 4), round(y1 / H, 4), round((x2 - x1) / W, 4), round((y2 - y1) / H, 4)],
                          "det": round(f["d"], 3), "px": px, "q": f.get("q", quality_without_crop(f["d"], px)), "e": len(embs)})
            if f.get("embedding_missing"):
                found[-1]["embedding_missing"] = True
            vector = np.asarray(f["v"], dtype=np.float32)
            if len(vector) == 512 and dim == 1024:
                # Legacy rows have no AdaFace view: preserve their unit R50 vector,
                # with a zero AdaFace half (cross-model scores are conservative).
                vector = np.concatenate([unit(vector), np.zeros(512, dtype=np.float32)])
            if vector.shape != (dim,) or not np.isfinite(vector).all():
                raise ValueError("invalid cached embedding dimensions or values")
            embs.append(vector)
        rows.append({"photo": e["id"], "faces": found})
        if n % 25 == 0:
            print(f"{n}/{len(catalog)} photos, {len(embs)} faces", flush=True)
    np.save(DATA / "face_emb.npy", np.asarray(embs, dtype=np.float32).reshape(-1, dim))
    (DATA / "faces_raw.json").write_text(json.dumps(rows))
    print(f"done: {len(embs)} faces in {len(rows)} photos; references {ref_count}/{len(people)}; "
          f"missing portraits {ref_missing}; skipped missing {missing}, OOM {oom}")


def fixrefs():
    """Retry portraits where extract found no face: upscale small ones and accept weaker detections."""
    people = load_people()
    count, missing, changed = _references(people, _model_loader(), get_ada_session(), retry=True)
    _people_outputs(people)
    (DATA / "aliases.json").write_text(json.dumps(load_aliases(), sort_keys=True), encoding="utf-8")
    print(f"updated {changed} references, now {count}/{len(people)}; still missing {missing}")


def rank_persons(bank_e, bank_p, e, agg="max"):
    """Rank people by maximum or mean top-three cosine similarity."""
    if agg not in ("max", "top3"):
        raise ValueError("unknown aggregation")
    bank_p = np.asarray(bank_p)
    if not len(bank_p):
        return []
    sims = np.asarray(bank_e) @ e
    per = {}
    for p in sorted(set(bank_p)):
        scores = sims[bank_p == p]
        per[p] = float(scores.max() if agg == "max" else np.sort(scores)[-3:].mean())
    return sorted(per.items(), key=lambda kv: -kv[1])


def decide(ranked, px, t_high=T_HIGH, t_margin=T_MARGIN, t_cand=T_CAND, min_px=MIN_FACE_PX):
    """Return the automatic person, rounded score, and status."""
    if px < min_px:
        return None, 0.0, "tiny"
    if not ranked:
        return None, 0.0, "unknown"
    best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best[1] >= t_high and best[1] - second >= t_margin:
        return best[0], round(best[1], 3), "high"
    if best[1] >= t_cand:
        return None, round(best[1], 3), "candidate"
    return None, 0.0, "unknown"


def face_q(face):
    return face.get("q", quality_without_crop(face.get("det", 0.9), face["px"]))


def prototype_bank(raw, emb, ref_emb, ref_ids, face_fix):
    """Portraits, all confirmations, and one quality-weighted core prototype per person."""
    bank_e, bank_p = list(ref_emb), list(ref_ids)
    core = {}
    for row in raw:
        for i, face in enumerate(row["faces"]):
            if face.get("embedding_missing"):
                continue   # preserve historical labels without treating missing biometrics as references
            pid = face_fix.get(f"{row['photo']}:{i}")
            if pid and pid != "none":
                vector, q = emb[face["e"]], face_q(face)
                bank_e.append(vector); bank_p.append(pid)
                if q >= Q_CORE:
                    core.setdefault(pid, []).append((vector, q))
    for pid, samples in core.items():
        if len(samples) >= 2:
            vectors, weights = zip(*samples)
            prototype = np.average(vectors, axis=0, weights=weights)
            if np.linalg.norm(prototype) > 1e-12:
                bank_e.append(unit(prototype)); bank_p.append(pid)
    return np.asarray(bank_e), np.asarray(bank_p)


def cluster_pool(emb_matrix, q, photo_ids, method):
    """Cluster quality cores, attach nearby loose faces, then enforce cannot-link."""
    if method not in ("hdbscan", "average"):
        raise ValueError("unknown clustering method")
    n = len(emb_matrix)
    if len(q) != n or len(photo_ids) != n:
        raise ValueError("clustering inputs must have equal lengths")
    labels = np.full(n, -1, dtype=int)
    if n < 2:
        return labels.tolist()
    e = np.asarray(emb_matrix, dtype=np.float32)
    if e.ndim != 2 or not np.isfinite(e).all() or not np.isfinite(q).all():
        raise ValueError("clustering requires finite embeddings and quality")
    norms = np.linalg.norm(e, axis=1)
    valid = norms > 1e-12
    e = unit(e)
    core = np.flatnonzero((np.asarray(q) >= Q_CORE) & valid)
    if len(core) < 2:
        return labels.tolist()
    if method == "hdbscan":
        from sklearn.cluster import HDBSCAN
        labels[core] = HDBSCAN(min_cluster_size=2, metric="euclidean", copy=True).fit_predict(e[core])
        if np.all(labels[core] == -1):
            # HDBSCAN normally rejects a lone identity (including a pair).
            # Permit it only within the attachment radius, in Euclidean units.
            labels[core] = HDBSCAN(min_cluster_size=2, metric="euclidean", copy=True,
                                  allow_single_cluster=True,
                                  cluster_selection_epsilon=float(np.sqrt(2 * ATTACH_DIST))).fit_predict(e[core])
    else:
        from scipy.cluster.hierarchy import fcluster, linkage
        labels[core] = fcluster(linkage(e[core], "average", metric="cosine"), CLUSTER_DIST, "distance") - 1
    groups = [lab for lab in sorted(set(labels)) if lab >= 0 and np.count_nonzero(labels == lab) >= 2]
    labels[~np.isin(labels, groups)] = -1
    if not groups:
        return labels.tolist()
    centroids = np.asarray([unit(e[labels == lab].mean(axis=0)) for lab in groups])
    # Freeze core centroids so attachment cannot drift or depend on input order.
    loose = np.flatnonzero((labels == -1) & valid)
    for i in loose:
        distances = 1 - centroids @ e[i]
        nearest = int(np.argmin(distances))
        if distances[nearest] <= ATTACH_DIST:
            labels[i] = groups[nearest]
    for lab, centroid in zip(groups, centroids):
        by_photo = {}
        for i in np.flatnonzero(labels == lab):
            by_photo.setdefault(photo_ids[i], []).append(i)
        for members in by_photo.values():
            keep = max(members, key=lambda i: float(e[i] @ centroid))
            for i in members:
                if i != keep:
                    labels[i] = -1
        if np.count_nonzero(labels == lab) < MIN_CLUSTER:
            labels[labels == lab] = -1
    return labels.tolist()


def match():
    raw = json.loads((DATA / "faces_raw.json").read_text(encoding="utf-8"))
    emb = np.load(DATA / "face_emb.npy")
    ref_emb = np.load(DATA / "ref_emb.npy")
    ref_ids = json.loads((DATA / "ref_ids.json").read_text(encoding="utf-8"))
    corr_path = DATA / "corrections.json"
    corr = json.loads(corr_path.read_text(encoding="utf-8")) if corr_path.exists() else {}
    face_fix = corr.get("faces", {})   # "photoid:index" -> person id | "none"

    aliases_path = DATA / "aliases.json"
    if aliases_path.exists():
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        ref_ids = [aliases.get(pid, pid) for pid in ref_ids]
        face_fix = {key: aliases.get(pid, pid) if pid != "none" else pid for key, pid in face_fix.items()}

    bank_e, bank_p = prototype_bank(raw, emb, ref_emb, ref_ids, face_fix)

    out = {}
    by_face = {(row["photo"], i): f for row in raw for i, f in enumerate(row["faces"])}
    for row in raw:
        res = []
        for i, f in enumerate(row["faces"]):
            key = f"{row['photo']}:{i}"
            r = {"i": i, "bbox": f["bbox"], "person": None, "score": 0.0, "status": "unknown", "cands": [], "cluster": None}
            fixed = face_fix.get(key)
            ranked = rank_persons(bank_e, bank_p, emb[f["e"]])
            r["cands"] = [[p, round(s, 3)] for p, s in ranked[:3]]
            if fixed == "none":
                r["status"] = "rejected"
            elif fixed:
                r.update(person=fixed, status="confirmed", score=1.0)
            else:
                person, score, status = decide(ranked, f["px"], t_high=T_HIGH + (LOWQ_EXTRA if face_q(f) < Q_CORE else 0))
                r.update(person=person, score=score, status=status)
            res.append(r)
        # a person appears once per photo: keep the strongest claim, demote the rest to candidates
        seen = {}
        for r in res:
            if r["status"] == "high":
                prev = seen.get(r["person"])
                if prev is None or r["score"] > prev["score"]:
                    if prev: prev.update(person=None, status="candidate")
                    seen[r["person"]] = r
                else:
                    r.update(person=None, status="candidate")
        out[row["photo"]] = res

    # cluster every face that has no name yet, so one answer can name a whole cluster
    pool = [(pid, r) for pid, rs in out.items() for r in rs if r["person"] is None and r["status"] in ("unknown", "candidate")]
    if len(pool) > 1:
        entries = [by_face[pid, r["i"]] for pid, r in pool]
        e = np.array([emb[f["e"]] for f in entries])
        labels = cluster_pool(e, [face_q(f) for f in entries], [pid for pid, _ in pool], CLUSTER_METHOD)
        groups = {}
        for (pid, r), lab in zip(pool, labels):
            if lab >= 0:
                groups.setdefault(int(lab), []).append((r, auto_people.face_key(pid, r["bbox"])))
        groups = {lab: g for lab, g in groups.items() if len(g) >= MIN_CLUSTER}
        registry, floor = auto_people.load(DATA / "auto_people.json")
        mapping, registry = auto_people.assign_ids({lab: [k for _, k in g] for lab, g in groups.items()}, registry, floor)
        for lab, g in groups.items():
            for r, _ in g:
                r["cluster"] = int(mapping[lab][2:])   # stable across runs ("U_3" -> 3); stays an int so question ids and vote rules (c<digits>) keep working
        auto_people.save(DATA / "auto_people.json", registry)
    atomic_write(DATA / "faces.json", json.dumps(out))
    stats = {s: sum(1 for rs in out.values() for r in rs if r["status"] == s) for s in ("confirmed", "high", "candidate", "unknown", "tiny", "rejected")}
    print("faces:", sum(stats.values()), stats, "clusters:", len({r["cluster"] for rs in out.values() for r in rs if r["cluster"]}))


_index = {}
def raw_idx(raw, photo, i):
    if not _index:
        for row in raw:
            for j, f in enumerate(row["faces"]):
                _index[(row["photo"], j)] = f["e"]
    return _index[(photo, i)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["extract", "fixrefs", "match"])
    ap.add_argument("--reembed", action="store_true", help="ignore legacy photo caches during extract")
    a = ap.parse_args()
    if a.cmd == "extract":
        extract(reembed=a.reembed)
    else:
        {"fixrefs": fixrefs, "match": match}[a.cmd]()
