"""Scan source photos and write gallery/data/catalog.json (run before assets.py)."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

SRC = Path(r"C:\Users\Mr\Downloads\sorted_photos\sorted")
GALLERY = Path(__file__).resolve().parent.parent
OUT = GALLERY / "data" / "catalog.json"
GPS_IFD_TAG = 0x8825
# 8-digit run not touching other digits: matches IMG-YYYYMMDD-WA0000, IMG_YYYYMMDD, YYYYMMDD
DATE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def parse_date(name):
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def orientation(w, h):
    if abs(w / h - 1) <= 0.05:
        return "square"
    return "landscape" if w > h else "portrait"


def main():
    entries = []
    for path in SRC.glob("*/*.jpg"):
        with Image.open(path) as im:
            has_gps = GPS_IFD_TAG in im.getexif()
            im = ImageOps.exif_transpose(im)
            w, h = im.size
        name = path.name
        date = parse_date(name)
        entries.append({
            "id": "p" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8],
            "file": f"{path.parent.name}/{name}",
            "name": name,
            "scene": path.parent.name,
            "date": date,
            "width": w,
            "height": h,
            "orientation": orientation(w, h),
            "has_gps": has_gps,
        })

    ids = [e["id"] for e in entries]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RuntimeError(f"id collision: {sorted(dupes)}")

    entries.sort(key=lambda e: (e["date"] or "9999", e["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(entries)} entries to {OUT}")


if __name__ == "__main__":
    main()
