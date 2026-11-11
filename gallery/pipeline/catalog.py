"""Scan source photos and write gallery/data/catalog.json (run before assets.py)."""
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

GALLERY = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("RKMV_SOURCE", GALLERY.parent / "sorted"))  # source photos, one subfolder per album
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


def entry_for(path, rel_file, scene, extra=None):
    path = Path(path)
    with Image.open(path) as im:
        has_gps = GPS_IFD_TAG in im.getexif()
        exif = im.getexif()
        date = parse_date(path.name)
        if not date:
            raw_date = exif.get(36867)
            try:
                date = datetime.strptime(str(raw_date), "%Y:%m:%d %H:%M:%S").date().isoformat()
            except (TypeError, ValueError):
                pass
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    name = path.name
    entry = {"id": "p" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], "file": str(rel_file).replace("\\", "/"),
             "name": name, "scene": scene, "date": date, "width": w, "height": h,
             "orientation": orientation(w, h), "has_gps": has_gps}
    if extra:
        entry.update(extra)
    return entry


def add_entries(catalog, new):
    ids = {entry["id"] for entry in catalog}
    for entry in new:
        if entry["id"] in ids:
            raise RuntimeError("id collision")
        ids.add(entry["id"])
    return sorted([*catalog, *new], key=lambda e: (e.get("date") or "9999", e["name"], e["id"]))


def main():
    entries = []
    for path in SRC.glob("*/*.jpg"):
        entries.append(entry_for(path, f"{path.parent.name}/{path.name}", path.parent.name))

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
