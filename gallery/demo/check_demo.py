"""Validate the fabricated demo dataset in this folder. Run: python check_demo.py"""
import datetime
import json
import os
import re
from collections import Counter
from itertools import combinations

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES = {"campus-courtyard-exterior", "classroom", "corridor-stairs", "dining-hall",
          "exhibition-fair", "garden-fields", "hostel", "indoor-other", "library",
          "office-storeroom", "outdoor-other", "outing-trip", "playground-sports",
          "prayer-temple", "screenshots-collage", "stage-auditorium-hall", "unclear-selfie"}
TIERS = {"close", "friend", "acquaintance"}
REASONS = {"disagree", "vague", "low"}
HEX = re.compile(r"^#[0-9a-f]{6}$")
PHOTO_ID = re.compile(r"^p[0-9a-f]{8}$")
PHOTO_KEYS = {"id", "scene", "folder_scene", "scene_confirmed", "date", "w", "h", "color",
              "thumb", "web", "tw", "th", "activity", "objects", "mood", "crowd", "text",
              "alt", "faces", "people", "n_faces"}
PERSON_KEYS = {"id", "kind", "name", "reg_no", "section", "photos", "avatar", "community",
               "friends"}

passed = 0


def ok(cond, msg):
    global passed
    if not cond:
        raise AssertionError(msg)
    passed += 1


def load(name):
    with open(os.path.join(HERE, "data", name), encoding="utf-8") as f:
        return json.load(f)


def unit(v):
    return isinstance(v, (int, float)) and 0 <= v <= 1


def main():
    photos = load("photos.json")
    people = load("people.json")
    graph = load("graph.json")
    questions = load("questions.json")
    clusters = load("clusters.json")

    # counts
    ok(len(photos) == 60, f"photos.json has {len(photos)} entries, want 60")
    ok(len(people) == 24, f"people.json has {len(people)} entries, want 24")
    ok(len(questions) == 40, f"questions.json has {len(questions)} entries, want 40")
    by_type = Counter(q["type"] for q in questions)
    ok(by_type == {"scene": 20, "face": 14, "cluster": 6}, f"question mix {dict(by_type)}")
    ok(len(clusters) == 6, f"clusters.json has {len(clusters)} entries, want 6")

    # unique ids
    photo_by = {p["id"]: p for p in photos}
    ok(len(photo_by) == 60, "duplicate photo ids")
    person_by = {p["id"]: p for p in people}
    ok(len(person_by) == 24, "duplicate person ids")
    ok(set(person_by) == {f"S{9001 + i}" for i in range(24)}, "person ids not S9001..S9024")
    ok(len({q["id"] for q in questions}) == 40, "duplicate question ids")
    ok(len({c["id"] for c in clusters}) == 6, "duplicate cluster ids")

    # photos
    appearances = Counter()
    for ph in photos:
        pid = ph["id"]
        ok(PHOTO_KEYS <= set(ph), f"{pid} missing keys {PHOTO_KEYS - set(ph)}")
        ok(PHOTO_ID.match(pid), f"bad photo id {pid}")
        ok(ph["scene"] in SCENES, f"{pid} scene {ph['scene']}")
        ok(ph["folder_scene"] in SCENES, f"{pid} folder_scene {ph['folder_scene']}")
        ok(isinstance(ph["scene_confirmed"], bool), f"{pid} scene_confirmed not bool")
        d = datetime.date.fromisoformat(ph["date"])
        ok(2023 <= d.year <= 2026, f"{pid} date {ph['date']} outside 2023-2026")
        ok(HEX.match(ph["color"]), f"{pid} color {ph['color']}")
        ok(ph["thumb"] == f"img/thumb/{pid}.webp", f"{pid} thumb path")
        ok(ph["web"] == f"img/web/{pid}.webp", f"{pid} web path")
        ok(ph["tw"] == 400, f"{pid} tw {ph['tw']} != 400")
        ok(ph["th"] == round(400 * ph["h"] / ph["w"]), f"{pid} th inconsistent with w/h")
        ok(ph["n_faces"] == len(ph["people"]) == len(ph["faces"]), f"{pid} n_faces mismatch")
        ok(len(set(ph["people"])) == len(ph["people"]), f"{pid} duplicate people")
        for p in ph["people"]:
            ok(p in person_by, f"{pid} references unknown person {p}")
            appearances[p] += 1
        ok(sorted(f["person"] for f in ph["faces"]) == sorted(ph["people"]),
           f"{pid} faces do not match people")
        for i, f in enumerate(ph["faces"]):
            ok(f["i"] == i, f"{pid} face index {f['i']} != {i}")
            x, y, w, h = f["bbox"]
            ok(all(unit(v) for v in f["bbox"]) and w > 0 and h > 0
               and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9, f"{pid} face {i} bbox {f['bbox']}")
        # image files and their stated sizes
        for rel, size in ((ph["web"], (ph["w"], ph["h"])), (ph["thumb"], (ph["tw"], ph["th"]))):
            path = os.path.join(HERE, rel)
            ok(os.path.isfile(path), f"missing image {rel}")
            with Image.open(path) as im:
                ok(im.format == "WEBP", f"{rel} is {im.format}, want WEBP")
                ok(im.size == size, f"{rel} is {im.size}, want {size}")
                if rel == ph["web"]:
                    avg = im.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
                    want = tuple(int(ph["color"][j:j + 2], 16) for j in (1, 3, 5))
                    ok(all(abs(a - b) <= 3 for a, b in zip(avg, want)),
                       f"{pid} color {ph['color']} does not match image average {avg}")

    ok(len(os.listdir(os.path.join(HERE, "img", "web"))) == 60, "img/web file count")
    ok(len(os.listdir(os.path.join(HERE, "img", "thumb"))) == 60, "img/thumb file count")

    # people, avatars, graph
    ok({n["id"] for n in graph["nodes"]} == set(person_by), "graph nodes != people")
    node_by = {n["id"]: n for n in graph["nodes"]}
    for p in people:
        ok(PERSON_KEYS <= set(p), f"{p['id']} missing keys")
        ok(p["kind"] == "student", f"{p['id']} kind")
        ok(p["section"] in ("A", "B", "C"), f"{p['id']} section {p['section']}")
        ok(p["reg_no"] == p["id"][1:], f"{p['id']} reg_no {p['reg_no']}")
        ok(p["photos"] == appearances[p["id"]] > 0, f"{p['id']} photos count mismatch")
        ok(0 <= p["community"] <= 3, f"{p['id']} community")
        ok(p["avatar"] == f"img/people/{p['id']}.webp", f"{p['id']} avatar path")
        path = os.path.join(HERE, p["avatar"])
        ok(os.path.isfile(path), f"missing avatar {p['avatar']}")
        with Image.open(path) as im:
            ok(im.format == "WEBP" and im.size == (256, 256), f"{p['avatar']} format/size")
        n = node_by.get(p["id"])
        ok(n is not None and n["photos"] == p["photos"] and n["community"] == p["community"]
           and n["label"] == p["name"] and n["kind"] == "student", f"{p['id']} graph node")
    ok(len(os.listdir(os.path.join(HERE, "img", "people"))) == 24, "img/people file count")

    co = Counter()
    for ph in photos:
        co.update(combinations(sorted(ph["people"]), 2))
    edge_by = {}
    for e in graph["edges"]:
        key = (e["a"], e["b"])
        ok(e["a"] < e["b"] and e["a"] in person_by and e["b"] in person_by, f"edge {key}")
        ok(key not in edge_by, f"duplicate edge {key}")
        ok(e["tier"] in TIERS, f"edge {key} tier {e['tier']}")
        ok(e["shared"] == co[key] >= 1, f"edge {key} shared {e['shared']} != {co[key]}")
        want_w = round(e["shared"] / min(appearances[e["a"]], appearances[e["b"]]), 3)
        ok(abs(e["weight"] - want_w) < 1e-9, f"edge {key} weight {e['weight']} != {want_w}")
        edge_by[key] = e
    ok(len(edge_by) == len(co), "graph is missing co-appearing pairs")
    for p in people:
        pid = p["id"]
        neigh = [e for e in graph["edges"] if pid in (e["a"], e["b"])]
        other = lambda e: e["b"] if e["a"] == pid else e["a"]
        top = sorted(neigh, key=lambda e: (-e["weight"], -e["shared"], other(e)))[:3]
        ok(len(p["friends"]) == len(top) <= 3, f"{pid} friends count")
        for fr, e in zip(p["friends"], top):
            ok(fr["id"] == other(e) and fr["id"] != pid and fr["id"] in person_by,
               f"{pid} friend {fr['id']} is not the expected top neighbour")
            ok(fr["shared"] == e["shared"] and abs(fr["weight"] - e["weight"]) < 1e-9,
               f"{pid} friend {fr['id']} values disagree with graph edge")

    # questions
    seen_faces = set()
    scene_photos = set()
    for q in questions:
        qt = q["type"]
        ok(isinstance(q["priority"], float), f"{q['id']} priority not float")
        if qt == "scene":
            ok(q["id"] == "s" + q["photo"] and q["photo"] in photo_by, f"{q['id']} photo")
            ok(q["photo"] not in scene_photos, f"{q['id']} repeats a photo")
            scene_photos.add(q["photo"])
            ok(q["reason"] in REASONS, f"{q['id']} reason {q['reason']}")
            ok(len(q["options"]) == 3 and len(set(q["options"])) == 3
               and set(q["options"]) <= SCENES, f"{q['id']} options {q['options']}")
            ok(set(q["all"]) == SCENES and len(q["all"]) == 17, f"{q['id']} all")
        elif qt == "face":
            ph = photo_by.get(q["photo"])
            ok(ph is not None, f"{q['id']} unknown photo")
            ok(0 <= q["i"] < len(ph["faces"]), f"{q['id']} face index")
            f = ph["faces"][q["i"]]
            ok(q["id"] == f"f{q['photo']}:{q['i']}", f"{q['id']} id format")
            ok(q["bbox"] == f["bbox"] and all(unit(v) for v in q["bbox"]), f"{q['id']} bbox")
            ok(len(q["cands"]) == 3 and len(set(q["cands"])) == 3
               and all(c in person_by for c in q["cands"]), f"{q['id']} cands")
            ok(f["person"] in q["cands"], f"{q['id']} true person not among cands")
            key = f"{q['photo']}:{q['i']}"
            ok(key not in seen_faces, f"{q['id']} face reused")
            seen_faces.add(key)
        elif qt == "cluster":
            ok(q["id"] == f"c{q['cluster']}", f"{q['id']} id format")
            ok(len(q["faces"]) == q["size"], f"{q['id']} size != len(faces)")
            ok(len(q["cands"]) == 3 and len(set(q["cands"])) == 3
               and all(c in person_by for c in q["cands"]), f"{q['id']} cands")
            for ref in q["faces"]:
                ph_id, i = ref.split(":")
                ph = photo_by.get(ph_id)
                ok(ph is not None and int(i) < len(ph["faces"]), f"{q['id']} face ref {ref}")
                ok(ref not in seen_faces, f"{q['id']} face reused {ref}")
                seen_faces.add(ref)
            ok(len(q["sample"]) <= q["size"], f"{q['id']} sample too long")
            for s in q["sample"]:
                ref = f"{s['photo']}:{s['i']}"
                ok(ref in q["faces"], f"{q['id']} sample {ref} not in faces")
                ok(s["bbox"] == photo_by[s["photo"]]["faces"][s["i"]]["bbox"],
                   f"{q['id']} sample bbox mismatch")
        else:
            raise AssertionError(f"{q['id']} unknown type {qt}")
    ok(len({q["id"] for q in questions}) == 40, "question ids")

    qc = {q["cluster"]: q for q in questions if q["type"] == "cluster"}
    ok(set(qc) == {c["id"] for c in clusters}, "clusters.json ids != cluster questions")
    for c in clusters:
        q = qc[c["id"]]
        ok(c["size"] == q["size"] and c["cands"] == q["cands"] and c["sample"] == q["sample"],
           f"clusters.json {c['id']} disagrees with questions.json")

    print(f"PASS: {passed} checks. photos={len(photos)} people={len(people)} "
          f"edges={len(graph['edges'])} questions={len(questions)} clusters={len(clusters)}")


if __name__ == "__main__":
    main()
