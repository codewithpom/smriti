"""Face pipeline.

  python pipeline/faces.py extract   # slow: detect + embed every face (InsightFace buffalo_l), writes embeddings
  python pipeline/faces.py match     # fast: match to the student/staff list, apply corrections.json, cluster unknowns

Names come only from D:\\hello (portraits + name/reg/section). Nothing else from that folder is used.
"""
import argparse
import gc
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

GALLERY = Path(__file__).resolve().parent.parent
DATA = GALLERY / "data"
SRC = Path(r"C:\Users\Mr\Downloads\sorted_photos\sorted")
HELLO = Path(r"D:\hello\data")

MAX_SIDE = 2560          # detection resolution cap; small faces in group shots need pixels
MIN_DET = 0.60           # detector confidence floor
MIN_FACE_PX = 28         # faces smaller than this (in the 2560-capped image) are not matched

T_HIGH = 0.42            # cosine similarity for a confident match (portrait vs candid is a hard pair)
T_MARGIN = 0.06          # best must beat the second-best person by this
T_CAND = 0.28            # below this a face is unknown; between is a question candidate
CLUSTER_DIST = 0.55      # cosine distance for grouping unknown faces


def load_people():
    ids = json.loads((HELLO / "identities.json").read_text(encoding="utf-8"))
    people = {}
    for s in ids["students"]:
        pid = f"S{s['reg_no']}"
        people[pid] = {"id": pid, "kind": "student", "name": s["name"], "reg_no": s["reg_no"],
                       "section": s.get("section"), "portrait_src": HELLO / "reference_faces" / "students" / f"{s['reg_no']}.jpg"}
    for t in ids["staff"]:
        people[t["id"]] = {"id": t["id"], "kind": "staff", "name": t["name"], "role": t.get("role"),
                           "portrait_src": next((HELLO / "reference_faces" / "staff").glob(t["id"] + "_*.jpg"), None)}
    extra = DATA / "extra_students.json"   # students fetched later by reg number (name/section only, no marks)
    if extra.exists():
        for s in json.loads(extra.read_text(encoding="utf-8")):
            pid = f"S{s['reg_no']}"
            own = DATA / "extra_refs" / f"{s['reg_no']}.jpg"
            people[pid] = {"id": pid, "kind": "student", "name": s["name"], "reg_no": s["reg_no"], "section": s.get("section"),
                           "portrait_src": own if own.exists() else people.get(pid, {}).get("portrait_src")}
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


def extract():
    app = get_app()
    people = load_people()

    # reference portraits: largest face per portrait
    ref_ids, ref_emb, ref_missing = [], [], []
    for pid, p in people.items():
        src = p["portrait_src"]
        if not src or not Path(src).exists():
            ref_missing.append(pid); continue
        faces = app.get(load_rgb(src, 1280))
        if not faces:
            ref_missing.append(pid); continue
        f = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        ref_ids.append(pid); ref_emb.append(f.normed_embedding)
    np.save(DATA / "ref_emb.npy", np.array(ref_emb, dtype=np.float32))
    (DATA / "ref_ids.json").write_text(json.dumps(ref_ids))

    # portraits as small avatars
    out = GALLERY / "img" / "people"; out.mkdir(parents=True, exist_ok=True)
    for pid, p in people.items():
        src = p["portrait_src"]
        if src and Path(src).exists():
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB"); im.thumbnail((240, 240))
                im.save(out / f"{pid}.webp", quality=80)

    catalog = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    cache = DATA / "fx"; cache.mkdir(exist_ok=True)   # one file per photo: the run resumes after a crash
    rows, embs = [], []
    for n, e in enumerate(catalog, 1):
        cf = cache / f"{e['id']}.json"
        if not cf.exists():
            for cap in (MAX_SIDE, 1600, 1024):          # low-RAM machine: retry smaller on OOM
                try:
                    img = load_rgb(SRC / e["file"], cap)
                    H, W = img.shape[:2]
                    faces = app.get(img)
                    break
                except (MemoryError, cv2_error()):
                    gc.collect(); faces = None
            if faces is None:
                print("skip (OOM)", e["id"], flush=True); continue
            cf.write_text(json.dumps([{"b": [float(v) for v in f.bbox], "w": W, "h": H, "d": float(f.det_score),
                                       "v": f.normed_embedding.tolist()} for f in faces]))
        found = []
        for f in json.loads(cf.read_text()):
            x1, y1, x2, y2 = f["b"]; W, H = f["w"], f["h"]
            if f["d"] < MIN_DET:
                continue
            found.append({"bbox": [round(x1 / W, 4), round(y1 / H, 4), round((x2 - x1) / W, 4), round((y2 - y1) / H, 4)],
                          "det": round(f["d"], 3), "px": int(min(x2 - x1, y2 - y1)), "e": len(embs)})
            embs.append(np.array(f["v"], dtype=np.float32))
        rows.append({"photo": e["id"], "faces": found})
        if n % 25 == 0:
            print(f"{n}/{len(catalog)} photos, {len(embs)} faces", flush=True)
    np.save(DATA / "face_emb.npy", np.array(embs, dtype=np.float32))
    (DATA / "faces_raw.json").write_text(json.dumps(rows))
    (DATA / "people_meta.json").write_text(json.dumps(
        {pid: {k: v for k, v in p.items() if k != "portrait_src"} for pid, p in people.items()}, ensure_ascii=False))
    print(f"done: {len(embs)} faces in {len(rows)} photos; references {len(ref_ids)}/{len(people)}; missing {ref_missing}")


def fixrefs():
    """Retry portraits where extract found no face: upscale small ones and accept weaker detections."""
    app = get_app(); app.det_model.det_thresh = 0.3
    people = load_people()
    ids = json.loads((DATA / "ref_ids.json").read_text()); emb = list(np.load(DATA / "ref_emb.npy"))
    added = []
    failed_path = DATA / "ref_failed.json"   # portraits that never yield a face: skipped next time (delete file to retry)
    failed = set(json.loads(failed_path.read_text())) if failed_path.exists() else set()
    for pid, p in people.items():
        src = p["portrait_src"]
        if pid in ids or pid in failed or not src or not Path(src).exists():
            continue
        for scale in (1.0, 1.6, 0.6):
            img = load_rgb(src, 1280)
            if scale != 1.0:
                import cv2
                img = cv2.resize(img, None, fx=scale, fy=scale)
            faces = app.get(img)
            if faces:
                f = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                ids.append(pid); emb.append(f.normed_embedding); added.append(pid); break
    failed |= {pid for pid, p in people.items() if pid not in ids and p["portrait_src"] and Path(p["portrait_src"]).exists()}
    failed_path.write_text(json.dumps(sorted(failed)))
    out = GALLERY / "img" / "people"; out.mkdir(parents=True, exist_ok=True)
    for pid, p in people.items():           # avatars + names for anyone added since `extract`
        src = p["portrait_src"]
        if src and Path(src).exists() and not (out / f"{pid}.webp").exists():
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB"); im.thumbnail((240, 240)); im.save(out / f"{pid}.webp", quality=80)
    (DATA / "people_meta.json").write_text(json.dumps(
        {pid: {k: v for k, v in p.items() if k != "portrait_src"} for pid, p in people.items()}, ensure_ascii=False))
    np.save(DATA / "ref_emb.npy", np.array(emb, dtype=np.float32)); (DATA / "ref_ids.json").write_text(json.dumps(ids))
    print(f"added {len(added)} references, now {len(ids)}/{len(people)}; still missing {sum(1 for p in people if p not in ids)}")


def match():
    from scipy.cluster.hierarchy import fcluster, linkage

    raw = json.loads((DATA / "faces_raw.json").read_text())
    emb = np.load(DATA / "face_emb.npy")
    ref_emb = np.load(DATA / "ref_emb.npy")
    ref_ids = json.loads((DATA / "ref_ids.json").read_text())
    corr_path = DATA / "corrections.json"
    corr = json.loads(corr_path.read_text()) if corr_path.exists() else {}
    face_fix = corr.get("faces", {})   # "photoid:index" -> person id | "none"

    # reference bank = portraits + faces the user confirmed (the learning loop)
    bank_e, bank_p = list(ref_emb), list(ref_ids)
    for row in raw:
        for i, f in enumerate(row["faces"]):
            pid = face_fix.get(f"{row['photo']}:{i}")
            if pid and pid != "none":
                bank_e.append(emb[f["e"]]); bank_p.append(pid)
    bank_e = np.array(bank_e); bank_p = np.array(bank_p)
    persons = sorted(set(bank_p))

    out, unknown_idx = {}, []
    for row in raw:
        res = []
        for i, f in enumerate(row["faces"]):
            key = f"{row['photo']}:{i}"
            r = {"i": i, "bbox": f["bbox"], "person": None, "score": 0.0, "status": "unknown", "cands": [], "cluster": None}
            fixed = face_fix.get(key)
            sims = bank_e @ emb[f["e"]]
            per = {p: float(sims[bank_p == p].max()) for p in persons}
            ranked = sorted(per.items(), key=lambda kv: -kv[1])
            r["cands"] = [[p, round(s, 3)] for p, s in ranked[:3]]
            if fixed == "none":
                r["status"] = "rejected"
            elif fixed:
                r.update(person=fixed, status="confirmed", score=1.0)
            elif f["px"] < MIN_FACE_PX:
                r["status"] = "tiny"
            else:
                best, second = ranked[0], ranked[1] if len(ranked) > 1 else (None, 0.0)
                if best[1] >= T_HIGH and best[1] - second[1] >= T_MARGIN:
                    r.update(person=best[0], score=round(best[1], 3), status="high")
                elif best[1] >= T_CAND:
                    r.update(score=round(best[1], 3), status="candidate")
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
        e = np.array([emb[raw_idx(raw, pid, r["i"])] for pid, r in pool])
        labels = fcluster(linkage(e, "average", metric="cosine"), CLUSTER_DIST, "distance")
        sizes = np.bincount(labels)
        for (pid, r), lab in zip(pool, labels):
            r["cluster"] = int(lab) if sizes[lab] >= 3 else None
    (DATA / "faces.json").write_text(json.dumps(out))
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
    a = ap.parse_args()
    {"extract": extract, "fixrefs": fixrefs, "match": match}[a.cmd]()
