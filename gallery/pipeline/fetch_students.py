"""Fetch student records and portraits for a registration-number range.

  python pipeline/fetch_students.py 10010 10335 --batch MB28 [--dry-run]

Adds new students to data/extra_students.json (reg_no, section, roll_sl, batch; the name stays
empty until a CSV supplies it) and saves portraits to data/extra_refs/<reg>.jpg. Marks, totals and
ranks that the lookup API returns are never stored. Prints counts only. Run `faces.py fixrefs`
afterwards so the new portraits become reference embeddings.
"""
import argparse, hashlib, io, json, os, sys, time, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

GALLERY = Path(__file__).resolve().parent.parent
API = "https://v0-student-profile-lookup.vercel.app/api/search?q={}"
IMG = "https://www.rkmvnarendrapur.org/student/ProfileImage/{}.JPG"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MIN_BYTES = 5000


def get(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except Exception:
            time.sleep(1 + k)
    return None, ""


def lookup(reg):
    body, _ = get(API.format(reg))
    try:
        rows = json.loads(body)["results"] if body else []
    except (ValueError, KeyError, TypeError):
        rows = []
    return rows[0] if rows else None


def portrait(reg):
    body, ctype = get(IMG.format(reg))
    if not body or len(body) < MIN_BYTES or not ctype.startswith("image/"):
        return None
    try:
        Image.open(io.BytesIO(body)).verify()
    except Exception:
        return None
    return body


def fetch_one(reg, delay):
    time.sleep(delay)
    return reg, lookup(reg), portrait(reg)


def run(start, end, batch, data, delay=0.2, workers=4, dry=False, fetch=fetch_one):
    data = Path(data)
    extra_path = data / "extra_students.json"
    extra = json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else []
    meta_path = data / "people_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    have = {str(s["reg_no"]) for s in extra} | {str(v.get("reg_no")) for v in meta.values() if v.get("reg_no")}
    todo = [r for r in range(start, end + 1) if str(r) not in have]
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda r: fetch(r, delay), todo))
    # the same image served for many numbers is a placeholder, not a portrait
    hashes = Counter(hashlib.sha1(img).hexdigest() for _, _, img in rows if img)
    added = []
    stats = Counter(already=len(range(start, end + 1)) - len(todo))
    for reg, rec, img in rows:
        if img and hashes[hashlib.sha1(img).hexdigest()] >= 3:
            img = None; stats["placeholder"] += 1
        if rec is None and img is None:
            stats["none"] += 1
            continue
        stats["with_api" if rec else "image_only"] += 1
        stats["portrait" if img else "no_portrait"] += 1
        entry = {"reg_no": str(reg), "name": "", "section": (rec or {}).get("sec"),
                 "roll_sl": (rec or {}).get("sl_no"), "batch": batch}
        added.append((entry, img))
    if not dry:
        (data / "extra_refs").mkdir(parents=True, exist_ok=True)
        for entry, img in added:
            if img:
                (data / "extra_refs" / f"{entry['reg_no']}.jpg").write_bytes(img)
        extra_path.write_text(json.dumps(extra + [e for e, _ in added], ensure_ascii=False, indent=1), encoding="utf-8")
    stats["new_students"] = len(added)
    return dict(stats)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("start", type=int); ap.add_argument("end", type=int)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--data", default=str(GALLERY / "data"))
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(run(a.start, a.end, a.batch, a.data, a.delay, dry=a.dry_run))


if __name__ == "__main__":
    main()
