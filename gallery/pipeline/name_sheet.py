"""Build an offline cluster naming sheet."""
import argparse
import hashlib
import html
import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

DATA = Path(__file__).resolve().parent.parent / "data"


def load(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def crop_box(bbox, w, h):
    x, y, bw, bh = bbox
    side = min(w, h, max(bw * w, bh * h) * 1.5 or 1)
    left = min(max(0, (x + bw / 2) * w - side / 2), w - side)
    top = min(max(0, (y + bh / 2) * h - side / 2), h - side)
    size = max(1, min(w, h, math.ceil(side)))
    left, top = min(round(left), w - size), min(round(top), h - size)
    return left, top, left + size, top + size


def pick_crops(members, embeddings, k_best=8, k_outlier=4):
    if not members:
        return []
    vectors = np.asarray([embeddings[m["e"]] for m in members])
    distances = np.linalg.norm(vectors - vectors.mean(axis=0), axis=1)
    far = sorted(range(len(members)), key=lambda i: (-distances[i], members[i]["key"]))[:k_outlier]
    best = sorted(range(len(members)), key=lambda i: (-members[i]["px"], -members[i]["det"], members[i]["key"]))[:k_best]
    return [(members[i]["key"], i in far) for i in dict.fromkeys(best + far)]


def cluster_members(faces):
    groups = {}
    for photo, items in faces.items():
        for face in items:
            if face.get("cluster") is not None:
                groups.setdefault(str(face["cluster"]), []).append(f"{photo}:{face['i']}")
    return groups


def fingerprint(clusters):
    # Keep cluster boundaries: reassignment must invalidate previously named IDs.
    value = [(str(cid), sorted(keys)) for cid, keys in clusters.items()]
    return hashlib.sha1(json.dumps(sorted(value), separators=(",", ":")).encode()).hexdigest()


def coverage(sizes):
    total, result = 0, []
    for size in sizes:
        total += size
        result.append(total)
    return result


def generate(data, out, top=80):
    data, out = Path(data), Path(out)
    faces, meta = load(data / "faces.json"), load(data / "people_meta.json")
    raw, photos = load(data / "faces_raw.json"), load(data / "photos.json")
    embeddings = np.load(data / "face_emb.npy", allow_pickle=False)
    refs, corrections = load(data / "ref_ids.json"), load(data / "corrections.json")
    records = {}
    raw = {p["photo"]: p["faces"] for p in raw}
    for photo, items in faces.items():
        for face in items:
            key = f"{photo}:{face['i']}"
            records[key] = {**face, **raw[photo][face["i"]], "key": key, "photo": photo}
    groups = cluster_members(faces)
    eligible = {c: keys for c, keys in groups.items()
                if not any(records[k]["status"] in ("confirmed", "rejected") for k in keys)}
    selected = dict(sorted(eligible.items(), key=lambda item: (-len(item[1]), int(item[0])))[:top])
    stamp = fingerprint(selected)
    photo_map = {p["id"]: p for p in photos} if isinstance(photos, list) else photos
    (out / "crops").mkdir(parents=True, exist_ok=True)
    (out / "people").mkdir(parents=True, exist_ok=True)
    avatars = set()   # reference portraits, so every option shows the face it stands for
    for pid in meta:
        source = data.parent / "img" / "people" / f"{pid}.webp"
        if source.is_file():
            target = out / "people" / f"{pid}.jpg"
            if not target.exists():
                with Image.open(source) as image:
                    image.convert("RGB").resize((96, 96)).save(target, quality=80)
            avatars.add(pid)
    escape = lambda text: html.escape(str(text), quote=True)

    def crops(keys, best=8, far=4):
        parts = []
        for key, outlier in pick_crops([records[k] for k in keys], embeddings, best, far):
            record = records[key]
            source = (data.parent / photo_map[record["photo"]]["web"]).resolve()
            if not source.is_relative_to(data.parent.resolve()):
                raise ValueError("image path outside gallery")
            filename = hashlib.sha1(key.encode()).hexdigest() + ".jpg"
            if not (out / "crops" / filename).exists():
                with Image.open(source) as image:
                    image.convert("RGB").crop(crop_box(record["bbox"], *image.size)).resize((128, 128)).save(out / "crops" / filename, quality=80)
            parts.append(f'<img src="crops/{filename}" class="{"outlier" if outlier else ""}" alt="{"outlier" if outlier else "face"}">')
        return "".join(parts)

    people = [{"id": pid, "name": person["name"], "kind": person["kind"],
               "batch": person.get("batch", ""), "reg": person.get("reg_no", ""),
               "avatar": (out / "people" / f"{pid}.jpg").is_file()}
              for pid, person in meta.items()]
    people.sort(key=lambda p: (p["kind"] != "student", p["name"].casefold(), p["id"]))
    avatars = {p["id"] for p in people if p["avatar"]}
    labels = {pid: f"{pid} {person['name']} ({person['kind']})" for pid, person in meta.items()}
    sections = ['<h1>Unnamed clusters</h1>']
    questions = {str(q["cluster"]): q for q in load(data / "questions.json") if q["type"] == "cluster"}
    for cid, keys in selected.items():
        suggestions = [pid for pid in questions.get(cid, {}).get("cands", []) if pid in labels][:3]
        buttons = "".join(f'<button class="suggest" data-key="{i}" data-person="{escape(pid)}">{f'<img src="people/{escape(pid)}.jpg" alt="">' if pid in avatars else '<span class="initials" aria-hidden="true">' + escape("".join(w[0] for w in meta[pid]["name"].split()[:2])) + '</span>'}<span>{escape(meta[pid]["name"])}</span><kbd>{i}</kbd></button>' for i, pid in enumerate(suggestions, 1))
        sections.append(f'<section class="cluster" data-cluster="{escape(cid)}" tabindex="0" role="group" aria-label="Cluster {escape(cid)}, {len(keys)} faces"><h2>Cluster {escape(cid)} &middot; {len(keys)} faces</h2><div class="crops">{crops(keys)}</div><div class="suggestions">{buttons}</div><p class="answer" aria-live="polite"></p><div class="picker"><input id="cluster-{escape(cid)}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="list-{escape(cid)}" aria-activedescendant="" aria-label="Person for cluster {escape(cid)}" placeholder="Search name or reg no. New: 10123 Name | student Name | staff Name" autocomplete="off"><div id="list-{escape(cid)}" role="listbox" hidden></div></div><button data-value="none">Not a person</button><button data-value="skip">Mixed, skip</button><button type="button" data-action="new">＋ New person (a)</button><button data-clear>Clear answer</button></section>')
    sections.append('<h1>Students with no portrait</h1>')
    for pid, person in meta.items():
        if person["kind"] != "student" or pid in refs:
            continue
        keys = [k for k, value in corrections.get("faces", {}).items() if value == pid]
        sections.append(f'<section><h2>{escape(person.get("reg_no", ""))} {escape(person["name"])}</h2><p>{len(keys)} confirmed faces</p>{crops([k for k in keys if k in records], 8, 0)}</section>')
    config = json.dumps({"fingerprint": stamp, "clusters": list(selected), "sizes": {c: len(k) for c, k in selected.items()}, "total": sum(map(len, eligible.values())), "people": people}).replace("</", "<\\/")
    for asset in ("sheet.css", "sheet.js"):
        shutil.copyfile(Path(__file__).parent / "sheet_assets" / asset, out / asset)
    page = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cluster naming</title><link rel="stylesheet" href="sheet.css"></head><body><header><p id="progress" role="status"></p><button id="download">Download answers</button><p>j/k move, 1-3 pick, / search, n not a person, s skip, x clear, a new person, ? help</p><div id="help" hidden>j / ArrowDown: next card; k / ArrowUp: previous card; 1-3: pick suggestion; / or Enter: search; n: not a person; a: add a new person (student: reg number then optional name; student without a reg number: the word student then the name, optional batch like MB27 first; staff: the word staff then the name); s: mixed, skip; x / Backspace / Delete: clear; ?: toggle help. In search: arrows highlight, Enter selects, Escape returns to card.</div></header><main>' + "".join(sections) + '</main><script>window.SHEET = ' + config + '</script><script src="sheet.js"></script></body></html>'
    version = hashlib.sha1((out / "sheet.js").read_bytes() + (out / "sheet.css").read_bytes()).hexdigest()[:8]   # defeats stale browser caches
    page = page.replace('href="sheet.css"', f'href="sheet.css?v={version}"').replace('src="sheet.js"', f'src="sheet.js?v={version}"')
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"clusters {len(selected)}, faces {sum(map(len, selected.values()))}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out/sheet")
    parser.add_argument("--top", type=int, default=80)
    args = parser.parse_args(argv)
    if args.top < 0:
        parser.error("top must be nonnegative")
    generate(args.data, args.out, args.top)


if __name__ == "__main__":
    main()
