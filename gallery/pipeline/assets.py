"""Generate web/thumb WebP images, dominant colours and thumb sizes; then verify.

Run catalog.py first. Usage: python assets.py [--force]
"""
import argparse
import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from catalog import OUT, GALLERY, SRC

IMG = GALLERY / "img"
WEB_DIR = IMG / "web"
THUMB_DIR = IMG / "thumb"


def fit(im, edge):
    out = im.copy()
    out.thumbnail((edge, edge), Image.Resampling.LANCZOS)  # never upscales
    return out


def process(entry, force):
    web = WEB_DIR / f"{entry['id']}.webp"
    thumb = THUMB_DIR / f"{entry['id']}.webp"
    with Image.open(SRC / entry["file"]) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")
    mean = np.asarray(im.resize((16, 16))).reshape(-1, 3).mean(axis=0).round().astype(int)
    if force or not web.exists():
        fit(im, 1600).save(web, "WEBP", quality=82, method=4)
    if force or not thumb.exists():
        fit(im, 480).save(thumb, "WEBP", quality=75)
    with Image.open(thumb) as t:
        tw, th = t.size
    return {"color": "#%02x%02x%02x" % tuple(mean), "thumb_w": tw, "thumb_h": th}


def process_ids(catalog, ids, force=False):
    selected = set(ids)
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    written = []
    for entry in catalog:
        if entry["id"] not in selected or not (SRC / entry["file"]).is_file():
            continue
        try:
            web = WEB_DIR / f"{entry['id']}.webp"
            thumb = THUMB_DIR / f"{entry['id']}.webp"
            before = {path for path in (web, thumb) if path.exists()}
            entry.update(process(entry, force))
            written.extend(path for path in (web, thumb) if path.exists() and (force or path not in before))
        except Exception as ex:
            errors.append(f"{entry['file']}: {ex!r}")
    if written:
        for path in random.sample(written, min(20, len(written))):
            with Image.open(path) as im:
                if len(im.getexif()) != 0:
                    raise RuntimeError("generated asset contains EXIF")
    return errors


def main(ids=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    catalog = json.loads(OUT.read_text(encoding="utf-8"))
    errors = process_ids(catalog, ids if ids is not None else [e["id"] for e in catalog], args.force)
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

    # verification
    webs = sorted(WEB_DIR.glob("*.webp"))
    thumbs = sorted(THUMB_DIR.glob("*.webp"))
    print(f"thumbs={len(thumbs)} webs={len(webs)} catalog={len(catalog)}")

    if ids is None:
        for p in random.sample(webs + thumbs, min(20, len(webs + thumbs))):
            with Image.open(p) as im:
                if len(im.getexif()) != 0:
                    raise SystemExit("EXIF found in generated asset")

    total_bytes = sum(p.stat().st_size for p in IMG.rglob("*") if p.is_file())
    print(f"img total: {total_bytes / 1e6:.1f} MB")
    print(f"has_gps true: {sum(1 for e in catalog if e['has_gps'])}")
    print("scenes:", dict(Counter(e["scene"] for e in catalog)))
    print(f"errors: {len(errors)}")
    for e in errors:
        print("  ", e)


if __name__ == "__main__":
    main()
