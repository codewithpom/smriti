"""Recover a deleted private archive on a trusted GitHub Actions runner.

Inputs and outputs are private local directories. The workflow encrypts outputs
before committing them. Logs deliberately contain counts and stages only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import build
import ci_scan
import dedupe
import faces
import obfuscate
import recover_archive
from remote import STATE_ALLOWLIST, open_remote


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_archive(old_photos, old_people, data):
    """Refuse a rebuild that loses any published photo, person, or known face."""
    photos = {p["id"]: p for p in read(data / "photos.json")}
    people = {p["id"]: p for p in read(data / "people.json")}
    if set(photos) != {p["id"] for p in old_photos}:
        raise RuntimeError("recovery changed the published photo set")
    if not {p["id"] for p in old_people}.issubset(people):
        raise RuntimeError("recovery lost people")
    checked = 0
    for old in old_photos:
        fresh = photos[old["id"]]
        if (fresh["w"], fresh["h"], fresh["date"]) != (old["w"], old["h"], old["date"]):
            raise RuntimeError("recovery changed historical photo metadata")
        by_index = {f["i"]: f for f in fresh["faces"]}
        for face in old["faces"]:
            current = by_index.get(face["i"])
            if current is None or current["bbox"] != face["bbox"]:
                raise RuntimeError("recovery changed historical face positions")
            if face["status"] in ("confirmed", "high"):
                if current["person"] != face["person"] or current["status"] not in ("confirmed", "high"):
                    raise RuntimeError("recovery lost a known face label")
                checked += 1
    return {"photos": len(photos), "people": len(people), "preserved_labels": checked}


def run(input_dir, backup_dir):
    archive = input_dir / "archive"
    gallery, data = ci_scan.GALLERY, ci_scan.DATA
    old_photos, old_people = read(archive / "photos.json"), read(archive / "people.json")
    print("recovery: restoring catalog and portraits", flush=True)
    stats = recover_archive.reconstruct(archive, data, gallery / "img", ci_scan.SRC,
                                       people_dir=ci_scan.PEOPLE, local_people_dir=input_dir / "local-people")
    print(json.dumps(stats, sort_keys=True), flush=True)
    print("recovery: computing duplicate hashes", flush=True)
    catalog = read(data / "catalog.json")
    for entry in catalog:
        info = dedupe.image_info(ci_scan.SRC / entry["file"])
        entry.update(sha=info["sha"], ph=info["ph"])
    (data / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    print("recovery: recomputing face embeddings", flush=True)
    faces.extract()
    counts = recover_archive.restore_face_layout(data, old_photos, model_tag=faces.MODEL_TAG)
    print(json.dumps(counts, sort_keys=True), flush=True)
    # Verify the persisted caches preserve historical face indices on future scans.
    faces.extract()
    labels = recover_archive.save_transferred_labels(data, old_photos, read(data / "faces_raw.json"), min_iou=0.99)
    if labels["unmatched_labels"]:
        raise RuntimeError("historical face labels could not be preserved")
    print("recovery: matching and clustering", flush=True)
    faces.match()
    build.main()
    report = {**stats, **counts, **verify_archive(old_photos, old_people, data)}
    (data / "recovery_report.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True), flush=True)

    key = os.environ.get("IMG_KEY", "")
    if len(key) < 32:
        raise RuntimeError("missing recovery image key")
    backup_dir.mkdir(parents=True, exist_ok=False)
    # Explicit selection: never include Cloudflare/GitHub tokens or cookies.
    shutil.copytree(data, backup_dir / "data")
    shutil.copytree(ci_scan.PEOPLE, backup_dir / "people")
    (backup_dir / "img-key.txt").write_text(key, encoding="utf-8")
    remote = open_remote()
    if remote.ls("pending/") or remote.ls("review/"):
        raise RuntimeError("new uploads arrived during recovery; retry before replacing pipeline state")
    with tempfile.TemporaryDirectory() as output:
        site = Path(output)
        obfuscate.build(gallery, site, key.encode(), require_files=False)
        # Keep deployed opaque URLs working after rotating the lost HMAC key.
        shutil.copytree(archive / "img", site / "img", dirs_exist_ok=True)
        image_paths = sorted(p.relative_to(site / "img").as_posix() for p in (site / "img").rglob("*") if p.is_file())
        digest = hashlib.sha256()
        for rel in image_paths:
            digest.update(rel.encode()); digest.update(hashlib.sha256((site / "img" / rel).read_bytes()).digest())
        print("recovery: seeding packed assets and private state", flush=True)
        remote.pack_put("state/published-assets.tar.gz", site / "img")
        remote.put_json("state/published-assets.json", {"sha256": digest.hexdigest(), "paths": image_paths})
        remote.pack_put("state/people.tar.gz", ci_scan.PEOPLE)
        with tempfile.TemporaryDirectory() as selected:
            state = Path(selected)
            for pattern in STATE_ALLOWLIST:
                for source in data.glob(pattern):
                    if source.is_file():
                        target = state / source.relative_to(data)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
            remote.push_dir(state, "state/")
        remote.pack_put("state/fx.tar.gz", data / "fx")
        shutil.copytree(site / "data", backup_dir / "site-data")
        if remote.budget:
            remote.budget.save()
    print("recovery: verified and seeded", flush=True)


def activate(directory):
    """Switch served JSON only after the corresponding static assets deploy."""
    names = ("photos", "people", "graph", "questions", "clusters")
    payloads = {name: (directory / f"{name}.json").read_bytes() for name in names}
    for value in payloads.values():
        json.loads(value)
    remote = open_remote()
    for name, payload in payloads.items():
        remote.put(f"data/{name}.json", payload, "application/json")
    if remote.budget:
        remote.budget.save()
    print("recovery: served gallery activated", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--activate", type=Path)
    args = parser.parse_args()
    try:
        if args.activate:
            activate(args.activate)
        elif args.input and args.backup:
            run(args.input, args.backup)
        else:
            parser.error("provide --input and --backup, or --activate")
        return 0
    except Exception as error:
        # No traceback: third-party errors can contain private photo/identity paths.
        print(f"recovery failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
