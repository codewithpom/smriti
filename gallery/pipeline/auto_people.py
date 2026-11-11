"""Stable ids for unnamed people (Immich / Google Photos style).

faces.py clusters unnamed faces, but cluster labels renumber on every run. This keeps a
registry {"U_3": [face keys]} so the same group of faces keeps the same id, can be named
later (U_3 -> real person via manual_aliases.json), and never reuses an id.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

OVERLAP = 0.5   # a new cluster inherits an id only if it holds MORE than this share of the old group


def face_key(photo, bbox):
    """Identity of a face that survives array re-indexing: photo + rounded normalised bbox."""
    raw = photo + "|" + ",".join(f"{v:.3f}" for v in bbox)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def next_number(registry, floor=1):
    nums = [int(k[2:]) for k in registry if k.startswith("U_") and k[2:].isdigit()]
    return max(max(nums, default=0) + 1, floor)


def assign_ids(clusters, registry, floor=1):
    """clusters {label: [face keys]}, registry {id: [face keys]} -> ({label: id}, new registry).

    Greedy one-to-one match by overlap size; ties go to the lower id. Unmatched clusters get
    fresh ids >= floor, so ids of vanished groups are never handed out again.
    """
    old = {gid: set(keys) for gid, keys in registry.items()}
    cands = []
    for label, keys in clusters.items():
        ks = set(keys)
        for gid, members in old.items():
            n = len(ks & members)
            if members and n / len(members) > OVERLAP:
                cands.append((-n, int(gid[2:]), label))
    mapping, used = {}, set()
    for _, num, label in sorted(cands, key=lambda c: (c[0], c[1], str(c[2]))):
        gid = f"U_{num}"
        if label in mapping or gid in used:
            continue
        mapping[label] = gid; used.add(gid)
    nxt = next_number(registry, floor)
    for label in sorted((l for l in clusters if l not in mapping), key=lambda l: min(clusters[l])):
        mapping[label] = f"U_{nxt}"; nxt += 1
    return mapping, {mapping[l]: sorted(clusters[l]) for l in clusters}


def load(path):
    p = Path(path)
    if not p.exists():
        return {}, 1
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("groups", {}), int(d.get("next", 1))


def save(path, registry):
    """Atomic write; keeps the id counter so ids are never reused."""
    p = Path(path)
    nxt = next_number(registry, load(p)[1])
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"next": nxt, "groups": registry}, f, sort_keys=True)
    os.replace(tmp, p)
