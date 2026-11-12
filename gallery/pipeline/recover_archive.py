"""Rebuild useful pipeline state from the deployed gallery archive.

The deployed JSON keeps stable photo/person ids and confirmed face decisions,
while its image paths are HMAC names. This module restores the local pipeline
layout without requiring the image key: downloaded archive images are matched
by their opaque deployed paths, and local originals are preferred when their
historical id (SHA1 of basename) matches.

Nothing is uploaded. Callers choose explicit output roots so recovery can be
reviewed before a normal build/deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
LABEL_STATUSES = {"confirmed", "high"}
PHOTO_ID_RE = re.compile(r"^p[0-9a-f]{8}$")
PERSON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_archive(photos, people):
    photo_ids = [p.get("id") for p in photos]
    if any(not isinstance(pid, str) or not PHOTO_ID_RE.fullmatch(pid) for pid in photo_ids) or len(photo_ids) != len(set(photo_ids)):
        raise ValueError("deployed photo ids must be unique p-prefixed 8-hex ids")
    person_ids = [p.get("id") for p in people]
    if any(not isinstance(pid, str) or not PERSON_ID_RE.fullmatch(pid) for pid in person_ids) or len(person_ids) != len(set(person_ids)):
        raise ValueError("deployed person ids must be unique safe path segments")


def _safe_archive_image(root: Path, rel: str) -> Path:
    """Resolve an archive-relative image path, rejecting traversal and symlinks out."""
    if "\\" in rel:
        raise ValueError("unsafe deployed image path")
    relpath = PurePosixPath(rel)
    if relpath.is_absolute() or ".." in relpath.parts or not relpath.parts or relpath.parts[0] != "img":
        raise ValueError("unsafe deployed image path")
    path = root.joinpath(*relpath.parts)
    if not path.is_file():
        raise FileNotFoundError(path)
    resolved_root = root.resolve()
    if not path.resolve().is_relative_to(resolved_root):
        raise ValueError("deployed image resolves outside archive root")
    return path


def _photo_id_for_name(name: str) -> str:
    return "p" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def index_local_originals(roots):
    """Index originals by the legacy catalog id derived from each basename."""
    found = {}
    for root in map(Path, roots):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            pid = _photo_id_for_name(path.name)
            previous = found.get(pid)
            # sorted roots/files make duplicate-basename selection deterministic;
            # prefer the larger original if the same file was copied at different sizes.
            if previous is None or (path.stat().st_size, str(path).casefold()) > (previous.stat().st_size, str(previous).casefold()):
                found[pid] = path
    return found


def _dimensions(path: Path):
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return image.size


def _copy_image(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or src.read_bytes() != dst.read_bytes():
        shutil.copyfile(src, dst)


def _safe_scene(value):
    if isinstance(value, str) and value and "/" not in value and "\\" not in value and value not in {".", ".."}:
        return value
    return "recovered"


def _photo_duplicate(a: Path, b: Path):
    """Require a real perceptual duplicate before trusting a basename id match."""
    import dedupe
    try:
        return dedupe.classify(dedupe.image_info(a), dedupe.image_info(b)) == "duplicate"
    except (OSError, ValueError):
        return False


def _portrait_candidates(directory: Path, person_id: str, kind: str, reg_no=None):
    if kind == "student":
        stem = str(reg_no or "")
        target = directory / "reference_faces" / "students"
        patterns = [f"{stem}.*"] if stem else []
    else:
        target = directory / "reference_faces" / "staff"
        patterns = [f"{person_id}_*.jpg", f"{person_id}.*"]
    return sorted((p for pattern in patterns for p in target.glob(pattern) if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                  key=lambda p: (-p.stat().st_size, str(p).casefold()))


def reconstruct_people(archive_dir, people_dir, local_people_dir, data_dir, asset_dir):
    """Restore the complete deployed roster and usable reference portraits.

    Local roster entries and portraits are preferred. Deployed avatars fill
    gaps. The original local roster is retained, while people absent from that
    roster are represented with the extra_* files consumed by faces.load_people.
    """
    archive_dir, people_dir, local_people_dir, data_dir, asset_dir = map(
        Path, (archive_dir, people_dir, local_people_dir, data_dir, asset_dir))
    deployed = _load_json(archive_dir / "people.json")
    _validate_archive([], deployed)
    local_identity_path = local_people_dir / "identities.json"
    local = _load_json(local_identity_path) if local_identity_path.exists() else {"students": [], "staff": []}
    identities = {"students": [], "staff": []}
    local_students = {str(row.get("reg_no")): dict(row) for row in local.get("students", []) if row.get("reg_no")}
    local_staff = {row.get("id"): dict(row) for row in local.get("staff", []) if row.get("id")}
    local_staff_ids = set(local_staff)
    extra_students, extra_students_noreg, extra_staff = [], [], []
    expected_meta = {}
    portrait_count = missing_portraits = 0
    people_dir.mkdir(parents=True, exist_ok=True)
    refs_root = people_dir / "reference_faces"
    for kind in ("students", "staff"):
        (refs_root / kind).mkdir(parents=True, exist_ok=True)

    for person in deployed:
        pid = person["id"]
        kind = person.get("kind")
        name = str(person.get("name") or "")
        reg_no = str(person.get("reg_no") or "")
        if kind == "student":
            if reg_no and pid != f"S{reg_no}":
                raise ValueError("deployed student id does not match registration number")
            row = local_students.pop(reg_no, None) if reg_no else None
            if row is None:
                row = {"reg_no": reg_no, "name": name, "section": person.get("section")}
            else:
                row.setdefault("name", name)
                row.setdefault("section", person.get("section"))
            row = {k: row[k] for k in ("reg_no", "name", "section") if k in row}
            if reg_no:
                identities["students"].append(row)
                target = refs_root / "students" / f"{reg_no}.jpg"
            else:
                row.update(id=pid, batch=person.get("batch") or "MB28")
                extra_students_noreg.append(row)
                target = None
        elif kind == "staff":
            row = local_staff.pop(pid, None) or {"id": pid, "name": name, "role": person.get("role") or ""}
            row = {"id": pid, "name": row.get("name") or name, "role": row.get("role") or person.get("role") or ""}
            identities["staff"].append(row)
            target = refs_root / "staff" / f"{pid}_recovered.jpg"
            # Keep faces.load_people's required glob shape for existing ids.
        else:
            raise ValueError("deployed person has an unsupported kind")

        avatar = None
        if person.get("avatar"):
            try:
                avatar = _safe_archive_image(archive_dir, person["avatar"])
            except FileNotFoundError:
                pass
        local_candidates = _portrait_candidates(local_people_dir, pid, kind, reg_no)
        portrait_src = local_candidates[0] if local_candidates else avatar
        if target is None and kind == "student" and reg_no:
            target = data_dir / "extra_refs" / f"{reg_no}.jpg"
        if target is None and kind == "student" and not reg_no:
            target = data_dir / "extra_refs" / f"{pid}.jpg"
        if portrait_src and target:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Normalize deployed WebP avatars to the JPEG pattern consumed by faces.py.
            if portrait_src == avatar:
                with Image.open(portrait_src) as im:
                    im.convert("RGB").save(target, "JPEG", quality=94)
            else:
                _copy_image(portrait_src, target)
            portrait_count += 1
        else:
            missing_portraits += 1

        if kind == "staff" and pid not in local_staff_ids:
            extra_staff.append({"id": pid, "name": name, "role": person.get("role") or ""})
        elif kind == "student" and reg_no:
            # Also override base-roster batch/section metadata in faces.load_people.
            extra_students.append({"reg_no": reg_no, "name": name, "section": person.get("section"),
                                   "batch": person.get("batch") or "MB27"})
        expected_meta[pid] = {k: person[k] for k in ("id", "kind", "name", "role", "reg_no", "batch", "section") if k in person}

    # Retain old local aliases/roster entries. faces.load_people applies its normal alias rules.
    identities["students"].extend({k: row[k] for k in ("reg_no", "name", "section") if k in row} for row in local_students.values())
    identities["staff"].extend({k: row[k] for k in ("id", "name", "role") if k in row} for row in local_staff.values())
    identities["students"].sort(key=lambda row: row.get("reg_no", ""))
    identities["staff"].sort(key=lambda row: row.get("id", ""))

    people_dir.mkdir(parents=True, exist_ok=True)
    (people_dir / "identities.json").write_text(json.dumps(identities, ensure_ascii=False, indent=2), encoding="utf-8")
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, values in (("extra_students.json", extra_students), ("extra_students_noreg.json", extra_students_noreg),
                         ("extra_staff.json", extra_staff)):
        (data_dir / name).write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    # Preserve exact deployed identity metadata as a fallback/source of truth. faces.extract
    # regenerates this same map from the restored roster before matching.
    (data_dir / "people_meta.json").write_text(json.dumps(expected_meta, ensure_ascii=False), encoding="utf-8")
    return {"deployed_people": len(deployed), "restored_portraits": portrait_count,
            "missing_portraits": missing_portraits, "added_students": len(extra_students) + len(extra_students_noreg),
            "added_staff": len(extra_staff)}


def reconstruct(archive_dir, data_dir, asset_dir, source_dir, original_roots=(), people_dir=None, local_people_dir=None):
    """Restore catalog, deployable image paths, tags, and scene corrections.

    Returns counts only. ``archive_dir`` contains photos.json, people.json and
    the downloaded opaque img/ tree. Recovered catalog source files are copied
    to ``source_dir``; matching local originals win over deployed WebP files.
    """
    archive_dir, data_dir, asset_dir, source_dir = map(Path, (archive_dir, data_dir, asset_dir, source_dir))
    photos = _load_json(archive_dir / "photos.json")
    people = _load_json(archive_dir / "people.json")
    if not isinstance(photos, list) or not isinstance(people, list):
        raise ValueError("deployed photos and people must be arrays")
    _validate_archive(photos, people)

    originals = index_local_originals(original_roots)
    catalog, tags, scene_fixes = [], {}, {}
    original_count = archive_count = asset_count = missing_avatars = rejected_originals = 0
    for photo in photos:
        pid = photo["id"]
        deployed_web = _safe_archive_image(archive_dir, photo["web"])
        deployed_thumb = _safe_archive_image(archive_dir, photo["thumb"])
        candidate = originals.get(pid)
        original = candidate if candidate and _photo_duplicate(candidate, deployed_web) else None
        if candidate and original is None:
            rejected_originals += 1
        source = original or deployed_web
        if original:
            original_count += 1
        else:
            archive_count += 1
        source_name = original.name if original else f"{pid}{deployed_web.suffix.lower()}"
        scene = _safe_scene(photo.get("folder_scene"))
        rel_file = f"{scene}/{source_name}"
        source_target = source_dir / rel_file
        _copy_image(source, source_target)
        width, height = _dimensions(source_target)
        asset_web = asset_dir / "web" / f"{pid}.webp"
        asset_thumb = asset_dir / "thumb" / f"{pid}.webp"
        _copy_image(deployed_web, asset_web)
        _copy_image(deployed_thumb, asset_thumb)
        asset_count += 2

        entry = {
            "id": pid,
            "file": rel_file.replace("\\", "/"),
            "name": source_name,
            "scene": scene,
            "date": photo.get("date"),
            "width": photo.get("w", width),
            "height": photo.get("h", height),
            "orientation": "square" if abs(photo.get("w", width) / photo.get("h", height) - 1) <= 0.05 else ("landscape" if photo.get("w", width) > photo.get("h", height) else "portrait"),
            "has_gps": False,
            "color": photo.get("color", "#888888"),
            "thumb_w": photo.get("tw"),
            "thumb_h": photo.get("th"),
        }
        if photo.get("ph"):
            entry["ph"] = photo["ph"]
        catalog.append(entry)

        tags[pid] = {
            key: photo[key]
            for key in ("activity", "objects", "mood", "crowd", "text", "alt")
            if key in photo
        }
        tags[pid]["people"] = photo.get("crowd")
        tags[pid]["scene"] = photo.get("scene")
        tags[pid]["confidence"] = 1.0 if photo.get("scene_confirmed") else 0.0
        if photo.get("scene_confirmed") and photo.get("scene") != photo.get("folder_scene"):
            scene_fixes[pid] = photo["scene"]

    catalog.sort(key=lambda entry: (entry.get("date") or "9999", entry["name"], entry["id"]))
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "tags_shard_recovered.json").write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")
    corrections_path = data_dir / "corrections.json"
    corrections = _load_json(corrections_path) if corrections_path.exists() else {}
    corrections["scenes"] = {**corrections.get("scenes", {}), **scene_fixes}
    (data_dir / "corrections.json").write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")

    for person in people:
        pid = person["id"]
        rel = person.get("avatar")
        if not rel:
            continue
        try:
            deployed_avatar = _safe_archive_image(archive_dir, rel)
        except FileNotFoundError:
            # Some deploys omit an avatar. Keep the person record; a local portrait
            # can be regenerated later by the normal people-output step.
            missing_avatars += 1
            continue
        _copy_image(deployed_avatar, asset_dir / "people" / f"{pid}.webp")
        asset_count += 1

    counts = {
        "photos": len(catalog),
        "people": len(people),
        "matched_originals": original_count,
        "archive_sources": archive_count,
        "restored_assets": asset_count,
        "missing_avatars": missing_avatars,
        "rejected_originals": rejected_originals,
        "scene_corrections": len(scene_fixes),
    }
    if people_dir is not None:
        counts.update(reconstruct_people(archive_dir, people_dir, local_people_dir or Path(), data_dir, asset_dir))
    return counts


def bbox_iou(a, b):
    """Intersection over union for normalized [x, y, width, height] boxes."""
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, aw, ah = map(float, a)
    bx1, by1, bw, bh = map(float, b)
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = iw * ih
    union = max(0.0, aw) * max(0.0, ah) + max(0.0, bw) * max(0.0, bh) - intersection
    return intersection / union if union > 0 else 0.0


def _unique_bbox_pairs(old_faces, new_boxes, min_iou=0.90, min_margin=0.05):
    """Return unambiguous old-index -> new-index matches; ties stay unmatched."""
    proposals = {}
    for old_i, old in enumerate(old_faces):
        ranked = sorted(((bbox_iou(old.get("bbox", []), box), new_i)
                         for new_i, box in enumerate(new_boxes)), reverse=True)
        if not ranked or ranked[0][0] < min_iou:
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < min_margin:
            continue
        proposals.setdefault(ranked[0][1], []).append((old_i, ranked[0][0]))
    return {olds[0][0]: new_i for new_i, olds in proposals.items() if len(olds) == 1}


def restore_face_layout(data_dir, deployed_photos, model_tag="r50f", min_iou=0.90, min_margin=0.05):
    """Restore historical face indices/bboxes, inserting zero-vector miss markers.

    Run after faces.extract. Existing detections are matched to the deployed face
    boxes one-to-one. Every historical face keeps its order and exact bbox; any
    detector miss gets an explicit ``embedding_missing`` marker and a zero vector.
    New detections are appended after the historical layout. The model cache is
    rewritten in the same order so subsequent extraction preserves these indices.
    """
    import numpy as np

    data_dir = Path(data_dir)
    raw_path, emb_path = data_dir / "faces_raw.json", data_dir / "face_emb.npy"
    raw_rows = _load_json(raw_path) if raw_path.exists() else []
    embeddings = np.load(emb_path, allow_pickle=False) if emb_path.exists() else np.zeros((0, 512), dtype=np.float32)
    if embeddings.ndim != 2:
        raise ValueError("face embeddings must be a matrix")
    dim = embeddings.shape[1] if embeddings.shape[1] else (1024 if "ada" in model_tag else 512)
    if embeddings.shape[1] == 0:
        embeddings = np.zeros((0, dim), dtype=np.float32)
    raw_by_photo = {row["photo"]: row.get("faces", []) for row in raw_rows}
    cache_dir = data_dir / "fx" / model_tag
    catalog = _load_json(data_dir / "catalog.json") if (data_dir / "catalog.json").exists() else []
    catalog_by_id = {entry["id"]: entry for entry in catalog}
    rebuilt_rows, rebuilt_vectors = [], []
    cache_preserved = missing_embeddings = archived_faces = added_detections = 0

    for photo in deployed_photos:
        pid = photo["id"]
        detections = raw_by_photo.get(pid, [])
        cache_path = cache_dir / f"{pid}.json"
        cache_rows = _load_json(cache_path) if cache_path.exists() else []
        cache_wh = next(((int(c["w"]), int(c["h"])) for c in cache_rows if c.get("w") and c.get("h")), None)
        catalog_row = catalog_by_id.get(pid, {})
        w, h = cache_wh or (int(catalog_row.get("width") or photo.get("w") or 1),
                            int(catalog_row.get("height") or photo.get("h") or 1))
        cache_boxes, usable_cache = [], []
        for cache_i, cached in enumerate(cache_rows):
            if cached.get("embedding_missing"):
                continue
            x1, y1, x2, y2 = cached["b"]
            cw, ch = int(cached.get("w", w)), int(cached.get("h", h))
            cache_boxes.append([x1 / cw, y1 / ch, (x2 - x1) / cw, (y2 - y1) / ch])
            usable_cache.append(cache_i)
        old_faces = photo.get("faces", [])
        usable_raw = [i for i, face in enumerate(detections) if not face.get("embedding_missing")]
        raw_pairs = _unique_bbox_pairs(old_faces, [detections[i].get("bbox", []) for i in usable_raw], min_iou, min_margin)
        cache_pairs = _unique_bbox_pairs(old_faces, cache_boxes, min_iou, min_margin)
        used_raw = {usable_raw[i] for i in raw_pairs.values()}
        used_cache = {usable_cache[i] for i in cache_pairs.values()}
        new_raw, new_cache = [], []

        def append_raw(face, vector, is_missing=False):
            entry = dict(face)
            entry["e"] = len(rebuilt_vectors)
            if is_missing:
                entry["embedding_missing"] = True
                entry["q"] = 0.0
            else:
                entry.pop("embedding_missing", None)
            rebuilt_vectors.append(np.asarray(vector, dtype=np.float32))
            new_raw.append(entry)

        for old_i, old in enumerate(old_faces):
            archived_faces += 1
            if old_i in raw_pairs:
                raw_index = usable_raw[raw_pairs[old_i]]
                found = dict(detections[raw_index])
                found["bbox"] = list(old["bbox"])
                append_raw(found, embeddings[int(found["e"])])
            else:
                x, y, bw, bh = map(float, old["bbox"])
                px = max(0, round(min(bw * w, bh * h)))
                marker = {"bbox": list(old["bbox"]), "det": 0.0, "px": px, "q": 0.0,
                          "embedding_missing": True}
                append_raw(marker, np.zeros(dim, dtype=np.float32), is_missing=True)
                missing_embeddings += 1

            if old_i in raw_pairs and old_i in cache_pairs:
                cached = dict(cache_rows[usable_cache[cache_pairs[old_i]]])
                cw, ch = int(cached.get("w", w)), int(cached.get("h", h))
                x, y, bw, bh = map(float, old["bbox"])
                cached["b"] = [x * cw, y * ch, (x + bw) * cw, (y + bh) * ch]
                new_cache.append(cached)
                cache_preserved += 1
            elif old_i in raw_pairs:
                raw_index = usable_raw[raw_pairs[old_i]]
                found = detections[raw_index]
                vector = embeddings[int(found["e"])].astype(float).tolist()
                x, y, bw, bh = map(float, old["bbox"])
                new_cache.append({"b": [x * w, y * h, (x + bw) * w, (y + bh) * h],
                                  "k": [[0.0, 0.0]] * 5, "w": w, "h": h,
                                  "d": float(found.get("det", 0.9)), "q": float(found.get("q", 0.0)),
                                  "v": vector})
            else:
                x, y, bw, bh = map(float, old["bbox"])
                new_cache.append({"b": [x * w, y * h, (x + bw) * w, (y + bh) * h],
                                  "k": [[0.0, 0.0]] * 5, "w": w, "h": h, "d": 0.0,
                                  "q": 0.0, "v": [0.0] * dim, "embedding_missing": True})

        for index, face in enumerate(detections):
            if face.get("embedding_missing"):
                continue
            if index not in used_raw:
                append_raw(face, embeddings[int(face["e"])])
                added_detections += 1
        for index, cached in enumerate(cache_rows):
            if index not in used_cache and not cached.get("embedding_missing"):
                new_cache.append(cached)

        rebuilt_rows.append({"photo": pid, "faces": new_raw})
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(new_cache, separators=(",", ":")), encoding="utf-8")

    matrix = np.asarray(rebuilt_vectors, dtype=np.float32).reshape(-1, dim)
    np.save(emb_path, matrix)
    raw_path.write_text(json.dumps(rebuilt_rows, separators=(",", ":")), encoding="utf-8")
    repair_counts = repair_cached_embeddings(data_dir, deployed_photos, model_tag=model_tag)
    return {"archived_faces": archived_faces,
            "missing_embeddings": repair_counts["remaining_missing_embeddings"],
            "cache_matched_faces": cache_preserved, "new_detections": added_detections,
            **repair_counts}


def repair_cached_embeddings(data_dir, deployed_photos, model_tag="r50f", min_iou=0.75,
                              min_margin=0.05, min_det=0.60):
    """Recover historical embeddings from unused, high-confidence cached detections.

    This is data-only: it never opens an image or calls a detector. It expects
    ``restore_face_layout`` output, where the first N raw rows for each photo
    correspond to its N deployed faces and remaining rows are unmatched new
    detections. Only a mutual, unambiguous match above the relaxed IoU floor is
    promoted. Historical order and boxes remain exact; consumed extras are
    removed, embedding indices are rebuilt, and each cache is regenerated from
    the resulting raw rows so a later cached ``faces.extract`` stays aligned.
    """
    import numpy as np

    data_dir = Path(data_dir)
    raw_path, emb_path = data_dir / "faces_raw.json", data_dir / "face_emb.npy"
    if not raw_path.is_file() or not emb_path.is_file():
        raise ValueError("recovery embeddings are missing")
    raw_rows = _load_json(raw_path)
    embeddings = np.load(emb_path, allow_pickle=False)
    if embeddings.ndim != 2 or not np.isfinite(embeddings).all():
        raise ValueError("recovery embeddings are invalid")
    dim = embeddings.shape[1]
    raw_by_photo = {row["photo"]: row.get("faces", []) for row in raw_rows}
    if len(raw_by_photo) != len(raw_rows):
        raise ValueError("duplicate recovery photo rows")
    catalog = _load_json(data_dir / "catalog.json") if (data_dir / "catalog.json").exists() else []
    catalog_by_id = {entry["id"]: entry for entry in catalog}
    cache_dir = data_dir / "fx" / model_tag

    # Reject a corrupt source layout before rewriting any files.
    refs = [int(face["e"]) for row in raw_rows for face in row.get("faces", [])]
    if len(refs) != len(set(refs)) or any(index < 0 or index >= len(embeddings) for index in refs):
        raise ValueError("recovery embedding indices are invalid")

    rebuilt_rows, rebuilt_vectors, cache_outputs = [], [], {}
    repaired = remaining = removed_extras = archived_faces = 0
    for photo in deployed_photos:
        pid = photo["id"]
        old_faces = photo.get("faces", [])
        source_faces = raw_by_photo.get(pid)
        if source_faces is None or len(source_faces) < len(old_faces):
            raise ValueError("recovery historical face layout is incomplete")
        for old_i, old in enumerate(old_faces):
            if source_faces[old_i].get("bbox") != old.get("bbox"):
                raise ValueError("recovery historical face layout changed")
        cache_path = cache_dir / f"{pid}.json"
        cache_rows = _load_json(cache_path) if cache_path.is_file() else []
        cache_wh = next(((int(row["w"]), int(row["h"])) for row in cache_rows
                         if row.get("w") and row.get("h")), None)
        catalog_row = catalog_by_id.get(pid, {})
        width, height = cache_wh or (
            int(catalog_row.get("width") or photo.get("w") or 1),
            int(catalog_row.get("height") or photo.get("h") or 1),
        )
        if width < 1 or height < 1:
            raise ValueError("recovery cache dimensions are invalid")

        missing_indices = [i for i, face in enumerate(source_faces[:len(old_faces)])
                           if face.get("embedding_missing")]
        candidates = [(j, face) for j, face in enumerate(source_faces[len(old_faces):], start=len(old_faces))
                      if not face.get("embedding_missing") and float(face.get("det", 0.0)) >= min_det]
        pairs = _unique_bbox_pairs(
            [old_faces[i] for i in missing_indices],
            [face.get("bbox", []) for _, face in candidates],
            min_iou=min_iou,
            min_margin=min_margin,
        )
        # _unique_bbox_pairs already rejects both ambiguous old faces and
        # detections claimed by more than one old face.
        match_by_old = {missing_indices[old_local]: candidates[candidate_local_i][0]
                        for old_local, candidate_local_i in pairs.items()}
        consumed = set(match_by_old.values())

        new_faces, row_vectors = [], []
        for old_i, old in enumerate(old_faces):
            archived_faces += 1
            candidate_i = match_by_old.get(old_i)
            if candidate_i is not None:
                face = dict(source_faces[candidate_i])
                face["bbox"] = list(old["bbox"])
                face.pop("embedding_missing", None)
                source_e = int(face["e"])
                vector = embeddings[source_e]
                if float(np.linalg.norm(vector)) <= 1e-12:
                    raise ValueError("candidate recovery embedding is empty")
                repaired += 1
            else:
                face = dict(source_faces[old_i])
                face["bbox"] = list(old["bbox"])
                source_e = int(face["e"])
                if face.get("embedding_missing"):
                    vector = np.zeros(dim, dtype=np.float32)
                    face["q"] = 0.0
                    face["det"] = 0.0
                    remaining += 1
                else:
                    vector = embeddings[source_e]
            face["e"] = len(rebuilt_vectors)
            rebuilt_vectors.append(np.asarray(vector, dtype=np.float32))
            row_vectors.append(rebuilt_vectors[-1])
            new_faces.append(face)

        for source_i in range(len(old_faces), len(source_faces)):
            if source_i in consumed:
                removed_extras += 1
                continue
            face = dict(source_faces[source_i])
            source_e = int(face["e"])
            vector = embeddings[source_e]
            face["e"] = len(rebuilt_vectors)
            rebuilt_vectors.append(np.asarray(vector, dtype=np.float32))
            row_vectors.append(rebuilt_vectors[-1])
            new_faces.append(face)

        rebuilt_rows.append({"photo": pid, "faces": new_faces})
        # Synthesize the reusable cache from this exact raw layout. Keypoints
        # are not read by cached extraction; embeddings and boxes are preserved.
        cache_outputs[cache_path] = [
            {
                "b": [float(face["bbox"][0]) * width, float(face["bbox"][1]) * height,
                      (float(face["bbox"][0]) + float(face["bbox"][2])) * width,
                      (float(face["bbox"][1]) + float(face["bbox"][3])) * height],
                "k": [[0.0, 0.0]] * 5,
                "w": width,
                "h": height,
                "d": float(face.get("det", 0.0)),
                "q": float(face.get("q", 0.0)),
                "v": vector.astype(float).tolist(),
                **({"embedding_missing": True} if face.get("embedding_missing") else {}),
            }
            for face, vector in zip(new_faces, row_vectors)
        ]

    if set(raw_by_photo) != {photo["id"] for photo in deployed_photos}:
        raise ValueError("recovery photo rows do not match deployed catalog")
    matrix = np.asarray(rebuilt_vectors, dtype=np.float32).reshape(-1, dim)
    if not np.isfinite(matrix).all():
        raise ValueError("rebuilt embeddings are invalid")

    raw_path.write_text(json.dumps(rebuilt_rows, separators=(",", ":")), encoding="utf-8")
    np.save(emb_path, matrix)
    for cache_path, rows in cache_outputs.items():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    label_counts = save_transferred_labels(data_dir, deployed_photos, rebuilt_rows, min_iou=0.999)
    if label_counts["unmatched_labels"]:
        raise ValueError("cached repair could not preserve historical labels")
    return {
        "repaired_embeddings": repaired,
        "remaining_missing_embeddings": remaining,
        "removed_extra_detections": removed_extras,
        "archived_faces": archived_faces,
        **label_counts,
    }


def transfer_confirmed_labels(deployed_photos, raw_faces, min_iou=0.90):
    """Map served confirmed/high/rejected decisions onto regenerated detector indices.

    Matching is one-to-one by bbox IoU. If the best match is below threshold or
    tied, it is reported unresolved instead of risking a label on the wrong face.
    The returned mapping has the shape expected by corrections.json ``faces``.
    """
    if not 0 < min_iou <= 1:
        raise ValueError("min_iou must be in (0, 1]")
    raw_by_photo = {row["photo"]: row.get("faces", []) for row in raw_faces}
    fixes, unresolved = {}, []
    for photo in deployed_photos:
        pid = photo["id"]
        candidates = raw_by_photo.get(pid, [])
        used = set()
        for old in photo.get("faces", []):
            status, person = old.get("status"), old.get("person")
            if status not in LABEL_STATUSES and status != "rejected":
                continue
            scored = sorted(((bbox_iou(old.get("bbox", []), new.get("bbox", [])), i) for i, new in enumerate(candidates) if i not in used), reverse=True)
            if not scored or scored[0][0] < min_iou or (len(scored) > 1 and scored[0][0] == scored[1][0]):
                unresolved.append({"photo": pid, "old_i": old.get("i"), "reason": "no_unique_bbox_match"})
                continue
            _, index = scored[0]
            used.add(index)
            fixes[f"{pid}:{index}"] = "none" if status == "rejected" else person
    return fixes, unresolved


def save_transferred_labels(data_dir, deployed_photos, raw_faces, min_iou=0.90):
    """Merge safely matched live decisions into corrections.json and audit misses."""
    data_dir = Path(data_dir)
    corrections_path = data_dir / "corrections.json"
    corrections = _load_json(corrections_path) if corrections_path.exists() else {}
    fixes, unresolved = transfer_confirmed_labels(deployed_photos, raw_faces, min_iou)
    corrections["faces"] = {**corrections.get("faces", {}), **fixes}
    corrections_path.write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_path = data_dir / "recovery_unmatched_faces.json"
    audit_path.write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"transferred_labels": len(fixes), "unmatched_labels": len(unresolved)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="directory containing deployed JSON and img/")
    parser.add_argument("--data", type=Path, required=True, help="pipeline data output directory")
    parser.add_argument("--assets", type=Path, required=True, help="gallery img output directory")
    parser.add_argument("--source", type=Path, required=True, help="RKMV_SOURCE-compatible photo root")
    parser.add_argument("--original-root", type=Path, action="append", default=[], help="local original photo tree; may be repeated")
    parser.add_argument("--people", type=Path, help="RKMV_PEOPLE-compatible identities and portrait output root")
    parser.add_argument("--local-people", type=Path, help="existing identities.json/reference_faces source root")
    args = parser.parse_args(argv)
    counts = reconstruct(args.archive, args.data, args.assets, args.source, args.original_root,
                         people_dir=args.people, local_people_dir=args.local_people)
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
