"""Compare AdaFace IR50 with InsightFace on the faces you have already named.

  python pipeline/adaface_ab.py [--recompute]

Both models see the SAME aligned 112x112 crop of every named face and every portrait, so the only
difference is the model. Embeddings are cached in pipeline/out/ab/emb.npz. The comparison uses the
leave-the-whole-answer-out trials from eval_match.py and prints, per model, the best recall that keeps
precision at 98% and at 95%. Weights live in pipeline/.models/adaface_ir50_webface4m (not in git).
Prints counts and rates only, never names.
"""
import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np

import eval_match
import faces

HERE = Path(__file__).resolve().parent
MODEL = HERE / ".models" / "adaface_ir50_webface4m"
CACHE = HERE / "out" / "ab" / "emb.npz"


def load_adaface():
    import torch
    from safetensors.torch import load_file
    torch.set_num_threads(2)
    if "fvcore" not in sys.modules:   # model.py imports a FLOP counter it never calls on the forward path
        fv, fn = types.ModuleType("fvcore"), types.ModuleType("fvcore.nn")
        fn.flop_count = lambda *a, **k: None
        sys.modules.update({"fvcore": fv, "fvcore.nn": fn})
    spec =importlib.util.spec_from_file_location("ir_model", MODEL / "models" / "iresnet" / "model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    net = mod.IR_50(input_size=(112, 112), output_dim=512)
    prefix = "model.net."
    net.load_state_dict({k[len(prefix):]: v for k, v in load_file(MODEL / "model.safetensors").items() if k.startswith(prefix)})
    return net.eval()


def unit(v):
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    return v / (np.linalg.norm(v) or 1.0)


def embed_both(app, net, aligned_bgr):
    import torch
    ins = unit(app.models["recognition"].get_feat(aligned_bgr))
    x = torch.from_numpy(np.ascontiguousarray(aligned_bgr[:, :, ::-1])).permute(2, 0, 1).float().div(255).sub(0.5).div(0.5)
    with torch.no_grad():
        ada = unit(net(x[None]).numpy())
    return ins, ada


def align(app, img, want=None):
    """Detect on img, pick the face nearest `want` (x, y) or the largest, return the aligned 112 crop."""
    from insightface.utils import face_align
    boxes, kps = app.det_model.detect(img, max_num=0, metric="default")
    if boxes is None or not len(boxes):
        return None
    if want is None:
        i = int(np.argmax((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])))
    else:
        i = int(np.argmin(((boxes[:, 0] + boxes[:, 2]) / 2 - want[0]) ** 2 + ((boxes[:, 1] + boxes[:, 3]) / 2 - want[1]) ** 2))
    return face_align.norm_crop(img, kps[i], image_size=112)


def face_crop(app, img, bbox):
    import cv2
    H, W = img.shape[:2]
    x, y, w, h = bbox
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    side = max(max(w * W, h * H) * 1.8, 160)
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    crop = img[y0:int(min(H, cy + side / 2)), x0:int(min(W, cx + side / 2))]
    scale = max(1.0, 160 / min(crop.shape[:2]))
    if scale > 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale)
    return align(app, crop, ((cx - x0) * scale, (cy - y0) * scale))


def compute(data):
    app = faces.get_app()
    app.det_model.det_thresh = 0.3
    net = load_adaface()
    raw = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))
    corr = json.loads((data / "corrections.json").read_text(encoding="utf-8"))
    cat = {p["id"]: p["file"] for p in json.loads((data / "catalog.json").read_text(encoding="utf-8"))}
    truth = {k: v for k, v in corr.get("faces", {}).items() if v != "none"}
    by_photo = {r["photo"]: r["faces"] for r in raw}
    keys, tins, tada, px = [], [], [], []
    done = {}
    for n, (key, pid) in enumerate(sorted(truth.items())):
        photo, i = key.rsplit(":", 1)
        if photo not in cat or photo not in by_photo:
            continue
        if photo not in done:
            done = {photo: faces.load_rgb(faces.SRC / cat[photo], faces.MAX_SIDE)}   # keep one image in memory
        f = by_photo[photo][int(i)]
        aligned = face_crop(app, done[photo], f["bbox"])
        if aligned is None:
            continue
        a, b = embed_both(app, net, aligned)
        keys.append(key); tins.append(a); tada.append(b); px.append(f["px"])
        if n % 100 == 0:
            print(f"faces {n}/{len(truth)}", flush=True)
    people = faces.load_people()
    ref_ids = json.loads((data / "ref_ids.json").read_text(encoding="utf-8"))
    rids, rins, rada = [], [], []
    for pid in ref_ids:
        src = people.get(pid, {}).get("portrait_src")
        if not src or not Path(src).exists():
            continue
        aligned = None
        for scale in (1.0, 1.6):
            img = faces.load_rgb(src, 1280)
            if scale != 1.0:
                import cv2
                img = cv2.resize(img, None, fx=scale, fy=scale)
            aligned = align(app, img)
            if aligned is not None:
                break
        if aligned is None:
            continue
        a, b = embed_both(app, net, aligned)
        rids.append(pid); rins.append(a); rada.append(b)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, keys=np.array(keys), tins=np.array(tins), tada=np.array(tada), px=np.array(px),
             rids=np.array(rids), rins=np.array(rins), rada=np.array(rada))
    print(f"embedded {len(keys)} of {len(truth)} named faces and {len(rids)} of {len(ref_ids)} portraits")


def best_recall(trials, target):
    best = (0.0, None, None, 0)
    for margin in (0.0, 0.03, 0.06, 0.10):
        for r in eval_match.sweep(trials, [t / 100 for t in range(5, 96)], t_margin=margin):
            if r["accepted"] >= 5 and r["precision"] >= target and r["recall"] > best[0]:
                best = (r["recall"], r["threshold"], margin, r["accepted"])
    return best


def compare(data):
    z = np.load(CACHE, allow_pickle=False)
    keys = [str(k) for k in z["keys"]]
    raw0 = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))
    corr0 = json.loads((data / "corrections.json").read_text(encoding="utf-8"))
    rows = {}
    for n, key in enumerate(keys):
        photo, i = key.rsplit(":", 1)
        rows.setdefault(photo, {})[int(i)] = n
    raw = [{"photo": p, "faces": [{"bbox": [0, 0, 0, 0], "det": 0, "px": float(z["px"][idx[j]]) if j in idx else 0, "e": idx.get(j, 0)}
                                  for j in range(max(idx) + 1)]} for p, idx in rows.items()]
    corr = {"faces": {k: corr0["faces"][k] for k in keys}, "history": corr0.get("history", [])}
    variants = {
        "InsightFace (same crops)": (z["tins"], z["rins"]),
        "AdaFace IR50": (z["tada"], z["rada"]),
        "both, averaged": (np.hstack([z["tins"], z["tada"]]) / 2 ** 0.5, np.hstack([z["rins"], z["rada"]]) / 2 ** 0.5),
    }
    ids = [str(x) for x in z["rids"]]
    print(f"{'model':26} {'bank':15} {'trials':>6} {'recall@98%':>10} {'(cutoff,margin)':>16} {'recall@95%':>10}")
    for name, (emb, ref) in variants.items():
        for bank in ("refs", "refs+confirmed"):
            trials, st = eval_match.leave_one_out_trials(raw, emb.astype(np.float32), ref.astype(np.float32), ids, corr, bank, "max")
            r98, r95 = best_recall(trials, 0.98), best_recall(trials, 0.95)
            cut = f"({r98[1]:.2f},{r98[2]:.2f})" if r98[1] is not None else "none"
            print(f"{name:26} {bank:15} {len(trials):6d} {r98[0]:10.3f} {cut:>16} {r95[0]:10.3f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=faces.DATA)
    ap.add_argument("--recompute", action="store_true")
    a = ap.parse_args(argv)
    if a.recompute or not CACHE.exists():
        compute(a.data)
    compare(a.data)


if __name__ == "__main__":
    main()
