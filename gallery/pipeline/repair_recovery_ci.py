"""Repair recoverable face embeddings from stored detector caches on Actions.

This path uses cached detections only. It never runs InsightFace or opens a
source photo. It validates the live snapshot before updating private state and
activates the repaired JSON only after the matching state has been written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

import build
import ci_scan
import faces
import obfuscate
import recover_archive
from remote import CHUNK_SIZE, STATE_ALLOWLIST, open_remote


STATE_MANIFEST_KEY = "state/manifest/state.json"
BASELINE_META_KEY = "state/published-assets.json"
IMAGE_MANIFEST_KEY = "state/manifest/img.json"
SITE_JSON = ("photos", "people", "graph", "questions", "clusters")
MAX_KV_STORAGE_BYTES = 1 << 30
KV_STORAGE_RESERVE_BYTES = 32 * 1024 * 1024
ACTIVATION_WRITE_RESERVE = 5
TRANSACTION_FILE = "repair-transaction.json"


class RepairError(Exception):
    """Safe, stable diagnostic for public workflow logs."""


def _sha1_bytes(value: bytes) -> str:
    return hashlib.sha1(value).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _allowed_state_path(rel: str) -> bool:
    path = PurePosixPath(rel)
    return any(path.match(pattern) or ("/**" in pattern and rel.startswith(pattern.split("/**", 1)[0] + "/"))
               for pattern in STATE_ALLOWLIST)


def _state_files(data_dir: Path) -> dict[str, Path]:
    return {
        path.relative_to(data_dir).as_posix(): path
        for path in data_dir.rglob("*")
        if path.is_file() and _allowed_state_path(path.relative_to(data_dir).as_posix())
    }


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in root.rglob("*") if path.is_file()
    }


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(payload)


def _public_assets(value) -> set[str]:
    found = set()
    if isinstance(value, dict):
        for item in value.values():
            found.update(_public_assets(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_public_assets(item))
    elif isinstance(value, str) and value.startswith("img/"):
        rel = value[4:]
        if not rel or ".." in PurePosixPath(rel).parts or "\\" in rel or ":" in rel:
            raise RepairError("public_image_reference_invalid")
        found.add(rel)
    return found


def _same_people(old: list[dict], new: list[dict]) -> bool:
    old_rows = {row["id"]: (row.get("name"), row.get("kind")) for row in old}
    new_rows = {row["id"]: (row.get("name"), row.get("kind")) for row in new}
    return len(old_rows) == len(old) and old_rows == new_rows


def _assert_public_image_urls(old_photos: list[dict], new_photos: list[dict],
                               old_people: list[dict], new_people: list[dict]) -> None:
    def keyed(rows, key):
        result = {row["id"]: row for row in rows}
        if len(result) != len(rows):
            raise RepairError("public_image_roster_invalid")
        return result

    old_p, new_p = keyed(old_photos, "id"), keyed(new_photos, "id")
    old_people_by_id, new_people_by_id = keyed(old_people, "id"), keyed(new_people, "id")
    if set(old_p) != set(new_p) or set(old_people_by_id) != set(new_people_by_id):
        raise RepairError("public_image_roster_changed")
    if any(tuple(old_p[key].get(field) for field in ("web", "thumb")) !=
           tuple(new_p[key].get(field) for field in ("web", "thumb")) for key in old_p):
        raise RepairError("public_photo_urls_changed")
    if any(old_people_by_id[key].get("avatar") != new_people_by_id[key].get("avatar")
           for key in old_people_by_id):
        raise RepairError("public_avatar_urls_changed")


def _assert_recovery_snapshot(archive_photos: list[dict], archive_people: list[dict],
                              served_photos: list[dict], served_people: list[dict]) -> None:
    """Ensure the encrypted current site still includes every bootstrap invariant."""
    old = {row["id"]: row for row in archive_photos}
    current = {row["id"]: row for row in served_photos}
    if len(old) != len(archive_photos) or set(old) != set(current):
        raise RepairError("recovery_archive_photo_roster_mismatch")
    if not _same_people(archive_people, served_people):
        raise RepairError("recovery_archive_people_roster_mismatch")
    for photo_id, historical in old.items():
        served = current[photo_id]
        if any(historical.get(field) != served.get(field) for field in ("w", "h", "date")):
            raise RepairError("recovery_archive_photo_metadata_mismatch")
        faces_by_index = {face["i"]: face for face in served.get("faces", [])}
        if len(faces_by_index) != len(served.get("faces", [])):
            raise RepairError("recovery_archive_face_indices_invalid")
        for face in historical.get("faces", []):
            current_face = faces_by_index.get(face.get("i"))
            if current_face is None or current_face.get("bbox") != face.get("bbox"):
                raise RepairError("recovery_archive_face_position_mismatch")
            if face.get("status") in ("confirmed", "high"):
                if (current_face.get("person") != face.get("person")
                        or current_face.get("status") not in ("confirmed", "high")):
                    raise RepairError("recovery_archive_face_label_mismatch")
            elif face.get("status") == "rejected" and current_face.get("status") != "rejected":
                raise RepairError("recovery_archive_rejection_mismatch")


def _validate_historical(old_photos: list[dict], new_photos: list[dict],
                         old_people: list[dict], new_people: list[dict],
                         old_raw: list[dict], counts: dict) -> None:
    before = {row["id"]: row for row in old_photos}
    after = {row["id"]: row for row in new_photos}
    if len(before) != len(old_photos) or set(before) != set(after):
        raise RepairError("photo_roster_changed")
    if not _same_people(old_people, new_people):
        raise RepairError("people_roster_changed")
    for photo_id, old in before.items():
        current = after[photo_id]
        if any(old.get(key) != current.get(key) for key in ("w", "h", "date")):
            raise RepairError("photo_metadata_changed")
        old_faces = {face["i"]: face for face in old.get("faces", [])}
        new_faces = {face["i"]: face for face in current.get("faces", [])}
        if len(old_faces) != len(old.get("faces", [])):
            raise RepairError("face_indices_invalid")
        for index, old_face in old_faces.items():
            face = new_faces.get(index)
            if face is None or face.get("bbox") != old_face.get("bbox"):
                raise RepairError("historical_face_position_changed")
            if old_face.get("status") in ("confirmed", "high"):
                if face.get("person") != old_face.get("person") or face.get("status") not in ("confirmed", "high"):
                    raise RepairError("historical_face_label_changed")
            elif old_face.get("status") == "rejected" and face.get("status") != "rejected":
                raise RepairError("historical_rejection_changed")

    missing_before = sum(
        1 for row in old_raw for face in row.get("faces", []) if face.get("embedding_missing")
    )
    if (counts.get("unmatched_labels") != 0
            or counts.get("repaired_embeddings", -1) + counts.get("remaining_missing_embeddings", -1) != missing_before
            or counts.get("removed_extra_detections") != counts.get("repaired_embeddings")
            or counts.get("archived_faces") != sum(len(row.get("faces", [])) for row in old_photos)):
        raise RepairError("cached_repair_counts_invalid")


def _remap_gallery_data(data_dir: Path, output: Path, image_key: str) -> dict[str, Path]:
    gallery = data_dir.parent
    previous = (faces.GALLERY, faces.DATA, faces.HELLO, build.DATA)
    try:
        faces.GALLERY, faces.DATA, faces.HELLO = gallery, data_dir, gallery / "people"
        build.DATA = data_dir
        faces.match()
        build.main()
        output.mkdir(parents=True, exist_ok=True)
        obfuscate.build(gallery, output.parent, image_key.encode("utf-8"), require_files=False)
        generated = output.parent / "data"
        for name in (*SITE_JSON, "pending"):
            source = generated / f"{name}.json"
            if source.is_file():
                destination = output / source.name
                # obfuscate.build writes to output.parent/data, which is the
                # requested output itself for the repair staging layout.
                if source.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
        return {name: output / f"{name}.json" for name in SITE_JSON}
    finally:
        faces.GALLERY, faces.DATA, faces.HELLO, build.DATA = previous


def _check_queues_empty(remote) -> None:
    if remote.ls("pending/") or remote.ls("review/"):
        raise RepairError("uploads_or_reviews_pending")


def _verify_live_snapshot(remote, old_state: dict[str, Path], desired_state: dict[str, Path],
                          old_served: dict[str, object], desired_served: dict[str, object], *,
                          source_state_hashes: dict[str, str] | None = None,
                          source_state_sha1: dict[str, str] | None = None,
                          source_served_hashes: dict[str, str] | None = None) -> dict[str, str | None]:
    _check_queues_empty(remote)
    state_listing = dict(remote.ls("state/"))
    old_hashes = source_state_hashes or {name: _sha256_file(path) for name, path in old_state.items()}
    desired_hashes = {name: _sha256_file(path) for name, path in desired_state.items()}
    allowed_names = set(old_hashes) | set(desired_hashes)
    actual_names = {key[6:] for key in state_listing if key.startswith("state/") and _allowed_state_path(key[6:])}
    if not actual_names.issubset(allowed_names):
        raise RepairError("live_state_snapshot_changed")

    current_hashes: dict[str, str | None] = {}
    for name in allowed_names:
        value = remote.get(f"state/{name}")
        current = _sha256_bytes(value) if value is not None else None
        current_hashes[name] = current
        accepted = {old_hashes.get(name), desired_hashes.get(name)}
        if current not in accepted:
            raise RepairError("live_state_snapshot_changed")

    # A recovery seed's state manifest is a fast second check for all state
    # files. If absent, byte comparisons above remain authoritative.
    state_manifest = remote.get_json(STATE_MANIFEST_KEY, None)
    if state_manifest is not None:
        if not isinstance(state_manifest, dict):
            raise RepairError("live_state_manifest_invalid")
        for name, value in state_manifest.items():
            if name not in allowed_names:
                raise RepairError("live_state_snapshot_changed")
            accepted = {(source_state_sha1 or {}).get(name, _sha1_file(old_state[name]) if name in old_state else None),
                        _sha1_file(desired_state[name]) if name in desired_state else None}
            if value not in accepted:
                raise RepairError("live_state_snapshot_changed")

    for name in SITE_JSON:
        live = remote.get_json(f"data/{name}.json", None)
        accepted = {(source_served_hashes or {}).get(name, _json_hash(old_served[name])),
                    _json_hash(desired_served[name])}
        if live is None or _json_hash(live) not in accepted:
            raise RepairError("live_gallery_snapshot_changed")

    return current_hashes


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_units(size: int) -> int:
    return 1 if size <= CHUNK_SIZE else (size + CHUNK_SIZE - 1) // CHUNK_SIZE + 1


def _pack_tree(root: Path) -> bytes:
    import io

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return output.getvalue()


def _apply_writes(remote, uploads, deletes, state_manifest_payload, activation) -> None:
    """Write recovery state first and expose repaired gallery JSON last."""
    if uploads:
        remote.put_many(uploads)
    if deletes:
        remote.delete(deletes)
    if state_manifest_payload is not None:
        remote.put(STATE_MANIFEST_KEY, state_manifest_payload, "application/json")
    if activation:
        remote.put_many(activation)


def _validate_prepared_backup(backup: Path) -> dict[str, int]:
    transaction = _json(backup / "repairs" / TRANSACTION_FILE)
    if transaction.get("status") != "prepared_cloud_apply_pending":
        raise RepairError("prepared_recovery_metadata_invalid")
    data = backup / "data"
    actual_state = {name: _sha256_file(path) for name, path in _state_files(data).items()}
    if actual_state != transaction.get("desired_state_sha256"):
        raise RepairError("prepared_recovery_state_changed")
    for name in SITE_JSON:
        path = backup / "site-data" / f"{name}.json"
        if not path.is_file() or _json_hash(_json(path)) != transaction.get("desired_served_sha256", {}).get(name):
            raise RepairError("prepared_recovery_gallery_changed")
    counts = transaction.get("counts")
    if not isinstance(counts, dict):
        raise RepairError("prepared_recovery_counts_invalid")
    return counts


def _repair(backup: Path, original_archive: Path, remote=None, *,
            original_backup: Path | None = None, prepare: bool = False) -> dict[str, int]:
    backup = Path(backup)
    original_backup = Path(original_backup) if original_backup else backup
    data = backup / "data"
    original_data = original_backup / "data"
    site = original_backup / "site-data"
    image_key = os.environ.get("IMG_KEY", "")
    key_file = backup / "img-key.txt"
    if len(image_key) < 32 or not key_file.is_file() or key_file.read_text(encoding="utf-8").strip() != image_key:
        raise RepairError("recovery_image_key_mismatch")
    if not data.is_dir() or not site.is_dir():
        raise RepairError("recovery_backup_incomplete")

    old_photos = _json(original_archive / "photos.json")
    old_people = _json(original_archive / "people.json")
    old_raw = _json(original_data / "faces_raw.json")
    old_served = {name: _json(site / f"{name}.json") for name in SITE_JSON}
    _assert_recovery_snapshot(old_photos, old_people, old_served["photos"], old_served["people"])
    original_state = _state_files(original_data)
    original_fx_hashes = _tree_hashes(original_data / "fx")

    if prepare:
        counts = recover_archive.repair_cached_embeddings(data, old_photos, model_tag=faces.MODEL_TAG)
    else:
        counts = _json(backup / "repairs" / "counts.json")
    transaction = None if prepare else _json(backup / "repairs" / TRANSACTION_FILE)
    faces_json = data / "faces.json"
    if not faces_json.is_file():
        raise RepairError("cached_repair_output_missing")

    if prepare:
        desired_files = _remap_gallery_data(data, backup / "repairs" / "data", image_key)
        (backup / "repairs" / "counts.json").write_text(json.dumps(counts), encoding="utf-8")
        repaired_site = backup / "site-data"
        repaired_site.mkdir(parents=True, exist_ok=True)
        for name, source in desired_files.items():
            shutil.copy2(source, repaired_site / f"{name}.json")
    else:
        desired_files = {name: backup / "repairs" / "data" / f"{name}.json" for name in SITE_JSON}
    desired_served = {name: _json(path) for name, path in desired_files.items()}
    new_photos, new_people = _json(data / "photos.json"), _json(data / "people.json")
    if prepare:
        _validate_historical(old_photos, new_photos, old_people, new_people, old_raw, counts)
    _assert_public_image_urls(old_served["photos"], desired_served["photos"],
                              old_served["people"], desired_served["people"])

    # Face labels may change, but every reference must already exist in the
    # packed baseline or in the individual image namespace.
    desired_state = _state_files(data)
    if prepare:
        report_path = data / "recovery_report.json"
        report = _json(report_path) if report_path.is_file() else {}
        report.update({
            "missing_embeddings": counts.get("remaining_missing_embeddings", 0),
            "remaining_missing_embeddings": counts.get("remaining_missing_embeddings", 0),
            "repaired_embeddings": counts.get("repaired_embeddings", 0),
            "removed_extra_detections": counts.get("removed_extra_detections", 0),
            "transferred_labels": counts.get("transferred_labels", report.get("transferred_labels", 0)),
            "unmatched_labels": counts.get("unmatched_labels", 0),
        })
        report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        desired_state = _state_files(data)
        transaction = {
            "status": "prepared_cloud_apply_pending",
            "counts": counts,
            "source_state_sha256": {name: _sha256_file(path) for name, path in original_state.items()},
            "source_state_sha1": {name: _sha1_file(path) for name, path in original_state.items()},
            "desired_state_sha256": {name: _sha256_file(path) for name, path in desired_state.items()},
            "source_served_sha256": {name: _json_hash(old_served[name]) for name in SITE_JSON},
            "desired_served_sha256": {name: _json_hash(desired_served[name]) for name in SITE_JSON},
            "source_fx_sha256": original_fx_hashes,
            "desired_fx_sha256": _tree_hashes(data / "fx"),
        }
        (backup / "repairs" / TRANSACTION_FILE).write_text(
            json.dumps(transaction, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return counts
    remote = remote or open_remote()
    baseline = remote.get_json(BASELINE_META_KEY)
    if not isinstance(baseline, dict) or not isinstance(baseline.get("paths"), list):
        raise RepairError("image_baseline_metadata_missing")
    available = set(baseline["paths"])
    available.update(key[4:] for key, _ in remote.ls("img/"))
    for name in SITE_JSON:
        if not _public_assets(desired_served[name]).issubset(available):
            raise RepairError("new_static_assets_required")

    transaction = transaction or _json(backup / "repairs" / TRANSACTION_FILE)
    current_hashes = _verify_live_snapshot(
        remote, original_state, desired_state, old_served, desired_served,
        source_state_hashes=transaction.get("source_state_sha256"),
        source_state_sha1=transaction.get("source_state_sha1"),
        source_served_hashes=transaction.get("source_served_sha256"))
    desired_fx_hashes = _tree_hashes(data / "fx")
    old_fx_hashes = transaction.get("source_fx_sha256", original_fx_hashes)
    # Check that the packed cache is exactly old or fully repaired.
    with tempfile.TemporaryDirectory(prefix="smriti-repair-fx-live-") as temp_fx:
        live_fx = Path(temp_fx) / "fx"
        if not remote.unpack_get("state/fx.tar.gz", live_fx):
            raise RepairError("live_face_cache_missing")
        live_fx_hashes = _tree_hashes(live_fx)
    if live_fx_hashes not in (old_fx_hashes, desired_fx_hashes):
        raise RepairError("live_face_cache_changed")

    fx_payload = _pack_tree(data / "fx") if desired_fx_hashes != old_fx_hashes else None
    uploads = []
    deletes = []
    for name in sorted(set(original_state) | set(desired_state)):
        old_path, new_path = original_state.get(name), desired_state.get(name)
        current = current_hashes.get(name)
        wanted = _sha256_file(new_path) if new_path else None
        if current == wanted:
            continue
        if new_path is None:
            if current is not None:
                deletes.append(f"state/{name}")
            continue
        accepted_source = transaction.get("source_state_sha256", {}).get(name)
        accepted_old = _sha256_file(old_path) if old_path else None
        if current not in {accepted_old, accepted_source}:
            raise RepairError("live_state_snapshot_changed")
        uploads.append((f"state/{name}", new_path.read_bytes(), None))
    if fx_payload is not None and live_fx_hashes != desired_fx_hashes:
        uploads.append(("state/fx.tar.gz", fx_payload, "application/gzip"))

    current_state_manifest = remote.get_json(STATE_MANIFEST_KEY, {}) or {}
    if not isinstance(current_state_manifest, dict):
        raise RepairError("live_state_manifest_invalid")
    desired_state_manifest = {name: _sha1_file(path) for name, path in desired_state.items()}
    state_manifest_changed = current_state_manifest != desired_state_manifest
    activation = []
    for name in SITE_JSON:
        desired_payload = desired_files[name].read_bytes()
        current_value = remote.get_json(f"data/{name}.json", None)
        if current_value is not None and _json_hash(current_value) == transaction["desired_served_sha256"][name]:
            continue
        if current_value is None or _json_hash(current_value) != transaction["source_served_sha256"][name]:
            raise RepairError("live_gallery_snapshot_changed")
        activation.append((f"data/{name}.json", desired_payload, "application/json"))

    all_uploads = list(uploads)
    upload_units = sum(_write_units(len(payload)) for _, payload, _ in uploads)
    if state_manifest_changed:
        state_manifest_payload = json.dumps(desired_state_manifest, separators=(",", ":"), ensure_ascii=False).encode()
        upload_units += 1
    else:
        state_manifest_payload = b""
    upload_units += len(activation)
    budget = getattr(remote, "budget", None)
    if budget:
        budget.check("writes", upload_units)
        budget.check("deletes", len(deletes))

    existing = dict(remote.ls(""))
    added_bytes = sum(len(payload) for _, payload, _ in all_uploads)
    if sum(existing.values()) + added_bytes + KV_STORAGE_RESERVE_BYTES > MAX_KV_STORAGE_BYTES:
        raise RepairError("kv_storage_preflight_failed")
    _check_queues_empty(remote)

    _apply_writes(remote, uploads, deletes,
                  state_manifest_payload if state_manifest_changed else None, activation)

    # Persist the recovered backup only after KV state and served JSON are
    # in place. A retry can recognize either old or repaired bytes.
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("backup_dir", type=Path)
    prepare.add_argument("output_dir", type=Path)
    prepare.add_argument("original_archive", type=Path)
    apply = sub.add_parser("apply")
    apply.add_argument("backup_dir", type=Path)
    apply.add_argument("repaired_dir", type=Path)
    apply.add_argument("original_archive", type=Path)
    args = parser.parse_args(argv)
    remote = None
    try:
        if args.command == "prepare":
            if args.output_dir.exists():
                raise RepairError("repair_output_must_be_new")
            shutil.copytree(args.backup_dir, args.output_dir)
            if (args.backup_dir / "repairs" / TRANSACTION_FILE).exists():
                counts = _validate_prepared_backup(args.output_dir)
            else:
                counts = _repair(args.output_dir, args.original_archive,
                                 original_backup=args.backup_dir, prepare=True)
        else:
            _validate_prepared_backup(args.repaired_dir)
            remote = open_remote()
            counts = _repair(args.repaired_dir, args.original_archive, remote,
                             original_backup=args.backup_dir)
    except Exception as error:
        diagnostic = str(error) if isinstance(error, RepairError) else type(error).__name__
        print(f"cached recovery repair failed: {diagnostic}", file=sys.stderr)
        return 1
    finally:
        budget = getattr(remote, "budget", None) if remote else None
        if budget:
            budget.save()
    print("cached recovery repair complete: " + json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
