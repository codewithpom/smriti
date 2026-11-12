"""Copy a Cloudflare KV namespace to Firebase Storage without mutating KV.

The script is intentionally separate from ``remote.migrate``: migration needs
content hashes (not just listed sizes), a resumable ownership journal, stable
source snapshots, and no source deletes. It copies one logical object at a
time; KVRemote.get transparently joins chunked KV values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
from pathlib import Path
import sys
import tempfile
import time

from remote import FirebaseStorageRemote, KVRemote


MANIFEST_KEY = "migration/kv-to-firebase/manifest.json"
MANIFEST_VERSION = 1
CHECKPOINT_KEYS = 50


class MigrationError(Exception):
    """Safe diagnostic for migration errors; never includes object keys."""


class ReadOnlySource:
    """Expose only listing and reads from the source backend."""

    def __init__(self, remote):
        self.__remote = remote

    def ls(self, prefix=""):
        return self.__remote.ls(prefix)

    def get(self, key):
        return self.__remote.get(key)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _content_type(key: str) -> str:
    lower = key.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".gz"):
        return "application/gzip"
    suffix = PurePosixPath(lower).suffix
    return {
        ".json": "application/json",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".txt": "text/plain; charset=utf-8",
        ".sqlite3": "application/vnd.sqlite3",
    }.get(suffix, "application/octet-stream")


def _valid_key(key: str) -> bool:
    if not isinstance(key, str) or not key or "\\" in key or "\x00" in key:
        return False
    path = PurePosixPath(key)
    return not path.is_absolute() and ".." not in path.parts


def _inventory(remote) -> dict[str, int]:
    result = {}
    for key, size in remote.ls(""):
        if not _valid_key(key) or key in result:
            raise MigrationError("source_inventory_invalid")
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise MigrationError("source_inventory_invalid") from None
        if size < 0:
            raise MigrationError("source_inventory_invalid")
        result[key] = size
    return result


def _fingerprint(rows: dict[str, dict]) -> str:
    return _sha256(_json_bytes(rows))


def _manifest_read(destination):
    payload = destination.get(MANIFEST_KEY)
    if payload is None:
        return None
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MigrationError("migration_manifest_invalid") from None
    if (not isinstance(value, dict) or value.get("version") != MANIFEST_VERSION
            or not isinstance(value.get("owned_keys"), list)
            or not isinstance(value.get("inflight_put"), list)
            or not isinstance(value.get("inflight_delete"), list)):
        raise MigrationError("migration_manifest_invalid")
    for field in ("owned_keys", "inflight_put", "inflight_delete"):
        keys = value[field]
        if any(not _valid_key(key) or key == MANIFEST_KEY for key in keys) or len(keys) != len(set(keys)):
            raise MigrationError("migration_manifest_invalid")
    return value


def _manifest_write(destination, manifest) -> None:
    payload = _json_bytes(manifest)
    destination.put(MANIFEST_KEY, payload, "application/json")
    stored = destination.get(MANIFEST_KEY)
    if stored is None or _sha256(stored) != _sha256(payload):
        raise MigrationError("migration_manifest_verification_failed")


def _new_manifest(source_id: str, destination_id: str) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "source_id": source_id,
        "destination_id": destination_id,
        "status": "in_progress",
        "phase": "initial",
        "passes": 0,
        "stable": False,
        "inventory_sha256": None,
        "inventory": {},
        "owned_keys": [],
        "inflight_put": [],
        "inflight_delete": [],
        "last_counts": {},
    }


def _check_manifest_identity(manifest: dict, source_id: str, destination_id: str) -> None:
    if manifest.get("source_id") != source_id or manifest.get("destination_id") != destination_id:
        raise MigrationError("migration_manifest_identity_mismatch")


def _destination_inventory(destination) -> set[str]:
    return {key for key, _ in destination.ls("")}


def _check_destination_ownership(destination, manifest: dict) -> set[str]:
    actual = _destination_inventory(destination)
    actual.discard(MANIFEST_KEY)
    owned = set(manifest["owned_keys"])
    in_flight = set(manifest["inflight_put"]) | set(manifest["inflight_delete"])
    if actual - owned - in_flight:
        raise MigrationError("destination_ownership_ambiguous")
    return actual


def _copy_pass(source, destination, manifest: dict, checkpoint: int) -> dict:
    listed = _inventory(source)
    if MANIFEST_KEY in listed:
        raise MigrationError("reserved_source_key_collision")

    rows = {}
    writes = skipped = verified = read_misses = size_changes = bytes_written = 0
    changed_keys = []
    # Ownership validation already proves that every non-journaled destination
    # object is absent. Avoid an extra remote 404 request per source key on the
    # first copy; read destination objects only when resuming owned data.
    owned_before = set(manifest["owned_keys"]) | set(manifest["inflight_put"])
    with tempfile.TemporaryDirectory(prefix="smriti-kv-firebase-") as spool_dir:
        spool = Path(spool_dir)
        for key in sorted(listed):
            data = source.get(key)
            if data is None:
                read_misses += 1
                continue
            data = bytes(data)
            if len(data) != listed[key]:
                size_changes += 1
            digest = _sha256(data)
            rows[key] = {"size": len(data), "sha256": digest, "content_type": _content_type(key)}
            current = destination.get(key) if key in owned_before else None
            if current is not None and len(current) == len(data) and _sha256(current) == digest:
                skipped += 1
                verified += 1
                continue
            spool_path = spool / _sha256(key.encode("utf-8"))
            spool_path.write_bytes(data)
            changed_keys.append(key)

        owned = set(manifest["owned_keys"])
        # Journal each write batch before mutating Firebase. If an invocation
        # stops mid-batch, every possible partial object is explicitly owned.
        for start in range(0, len(changed_keys), checkpoint):
            batch = changed_keys[start:start + checkpoint]
            manifest["inflight_put"] = batch
            _manifest_write(destination, manifest)
            for key in batch:
                data = (spool / _sha256(key.encode("utf-8"))).read_bytes()
                digest = _sha256(data)
                destination.put(key, data, _content_type(key))
                written = destination.get(key)
                if written is None or len(written) != len(data) or _sha256(written) != digest:
                    raise MigrationError("destination_verification_failed")
                writes += 1
                verified += 1
                bytes_written += len(data)
                owned.add(key)
            manifest["owned_keys"] = sorted(owned)
            manifest["inflight_put"] = []
            _manifest_write(destination, manifest)

    end_inventory = _inventory(source)
    clean = not read_misses and not size_changes and listed == end_inventory
    return {
        "rows": rows,
        "fingerprint": _fingerprint(rows),
        "clean": clean,
        "listed": len(listed),
        "read_misses": read_misses,
        "size_changes": size_changes,
        "writes": writes,
        "skipped": skipped,
        "verified": verified,
        "bytes_written": bytes_written,
        "listed_changed": listed != end_inventory,
    }


def _verify_source_snapshot(source) -> dict:
    """Read and hash KV again to detect same-size changes during a copy pass."""
    listed = _inventory(source)
    if MANIFEST_KEY in listed:
        raise MigrationError("reserved_source_key_collision")
    rows, read_misses, size_changes = {}, 0, 0
    for key, expected_size in sorted(listed.items()):
        data = source.get(key)
        if data is None:
            read_misses += 1
            continue
        data = bytes(data)
        if len(data) != expected_size:
            size_changes += 1
        rows[key] = {"size": len(data), "sha256": _sha256(data), "content_type": _content_type(key)}
    end_inventory = _inventory(source)
    clean = not read_misses and not size_changes and listed == end_inventory
    return {"rows": rows, "fingerprint": _fingerprint(rows), "clean": clean,
            "read_misses": read_misses, "size_changes": size_changes,
            "listed_changed": listed != end_inventory}


def _delete_stale_destination_keys(source_keys: set[str], destination, manifest: dict) -> int:
    stale = sorted(set(manifest["owned_keys"]) - source_keys)
    if stale:
        manifest["inflight_delete"] = stale
        _manifest_write(destination, manifest)
        destination.delete(stale)
    remaining = _destination_inventory(destination)
    if remaining != source_keys | {MANIFEST_KEY}:
        raise MigrationError("destination_cleanup_verification_failed")
    if stale:
        manifest["owned_keys"] = sorted(set(manifest["owned_keys"]) - set(stale))
        manifest["inflight_delete"] = []
        _manifest_write(destination, manifest)
    return len(stale)


def _recover_journal(destination, manifest: dict) -> None:
    """Promote journaled partial writes/deletes to owned keys before retrying."""
    inflight = set(manifest["inflight_put"]) | set(manifest["inflight_delete"])
    if not inflight:
        return
    manifest["owned_keys"] = sorted(set(manifest["owned_keys"]) | inflight)
    manifest["inflight_put"] = []
    manifest["inflight_delete"] = []
    _manifest_write(destination, manifest)


def migrate(source, destination, *, phase: str, source_id: str,
            destination_id: str, max_passes: int = 4,
            checkpoint: int = CHECKPOINT_KEYS, settle_seconds: int = 75) -> dict:
    if phase not in ("initial", "delta"):
        raise MigrationError("invalid_phase")
    if max_passes < 1 or checkpoint < 1 or settle_seconds < 0:
        raise MigrationError("invalid_limits")

    source_inventory = _inventory(source)
    if MANIFEST_KEY in source_inventory:
        raise MigrationError("reserved_source_key_collision")
    manifest = _manifest_read(destination)
    actual = _destination_inventory(destination)
    if manifest is None:
        if actual:
            raise MigrationError("destination_not_empty")
        manifest = _new_manifest(source_id, destination_id)
        _manifest_write(destination, manifest)
    else:
        _check_manifest_identity(manifest, source_id, destination_id)
        _check_destination_ownership(destination, manifest)
        _recover_journal(destination, manifest)

    totals = {"passes": 0, "writes": 0, "skipped": 0, "verified": 0,
              "bytes_written": 0, "read_misses": 0, "size_changes": 0,
              "source_change_detections": 0, "deleted_destination_keys": 0,
              "quiet_window_seconds": 0}
    stable_result = None
    pass_limit = 1 if phase == "initial" else max_passes
    for _ in range(pass_limit):
        result = _copy_pass(source, destination, manifest, checkpoint)
        totals["passes"] += 1
        for field in ("writes", "skipped", "verified", "bytes_written", "read_misses", "size_changes"):
            totals[field] += result[field]

        if phase == "delta" and result["clean"] and settle_seconds:
            time.sleep(settle_seconds)
            totals["quiet_window_seconds"] += settle_seconds
        verified_source = _verify_source_snapshot(source) if phase == "delta" and result["clean"] else None
        is_stable = bool(verified_source and verified_source["clean"]
                         and verified_source["fingerprint"] == result["fingerprint"])
        if phase == "delta" and (not result["clean"] or (verified_source and not is_stable)):
            totals["source_change_detections"] += 1
        manifest["phase"] = phase
        manifest["passes"] = int(manifest.get("passes", 0)) + 1
        manifest["inventory"] = result["rows"]
        manifest["inventory_sha256"] = result["fingerprint"]
        manifest["stable"] = is_stable
        manifest["status"] = "stable" if is_stable else (
            ("initial_copy_complete" if result["clean"] else "initial_copy_unstable")
            if phase == "initial" else "syncing")
        manifest["last_counts"] = {key: result[key] for key in (
            "listed", "writes", "skipped", "verified", "bytes_written", "read_misses", "size_changes", "listed_changed")}
        manifest["last_counts"]["quiet_window_seconds"] = settle_seconds if phase == "delta" else 0
        _manifest_write(destination, manifest)

        if is_stable:
            source_keys = set(result["rows"])
            # Delete only previously journaled migration-owned objects, and
            # only after two complete consecutive inventories agree.
            totals["deleted_destination_keys"] = _delete_stale_destination_keys(source_keys, destination, manifest)
            manifest["stable"] = True
            manifest["status"] = "stable"
            _manifest_write(destination, manifest)
            stable_result = result
            break

    if phase == "delta" and stable_result is None:
        manifest["stable"] = False
        manifest["status"] = "unstable"
        _manifest_write(destination, manifest)
        totals["stable"] = False
        totals["complete"] = False
        return totals

    totals["stable"] = bool(stable_result)
    totals["complete"] = bool(manifest.get("last_counts", {}).get("read_misses", 0) == 0
                               and manifest.get("last_counts", {}).get("size_changes", 0) == 0
                               and not manifest.get("last_counts", {}).get("listed_changed", True))
    return totals


def _build_remotes(env=os.environ):
    if env.get("STORAGE_BACKEND", "kv") != "kv":
        raise MigrationError("migration_requires_kv_source")
    names = ("CLOUDFLARE_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CLOUDFLARE_API_TOKEN",
             "FIREBASE_STORAGE_BUCKET", "GOOGLE_APPLICATION_CREDENTIALS")
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise MigrationError("migration_configuration_missing")
    source = ReadOnlySource(KVRemote(env["CLOUDFLARE_ACCOUNT_ID"], env["CF_KV_NAMESPACE_ID"],
                                     env["CLOUDFLARE_API_TOKEN"]))
    destination = FirebaseStorageRemote(env["FIREBASE_STORAGE_BUCKET"], env["GOOGLE_APPLICATION_CREDENTIALS"])
    source_id = _sha256(f'{env["CLOUDFLARE_ACCOUNT_ID"]}/{env["CF_KV_NAMESPACE_ID"]}'.encode())
    return source, destination, source_id, env["FIREBASE_STORAGE_BUCKET"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("initial", "delta"), required=True)
    parser.add_argument("--max-passes", type=int, default=4)
    parser.add_argument("--checkpoint-keys", type=int, default=CHECKPOINT_KEYS)
    parser.add_argument("--settle-seconds", type=int, default=75)
    args = parser.parse_args(argv)
    try:
        source, destination, source_id, destination_id = _build_remotes()
        result = migrate(source, destination, phase=args.phase, source_id=source_id,
                         destination_id=destination_id, max_passes=args.max_passes,
                         checkpoint=args.checkpoint_keys, settle_seconds=args.settle_seconds)
    except Exception as error:
        reason = str(error) if isinstance(error, MigrationError) else type(error).__name__
        print(json.dumps({"ok": False, "error": reason}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": result["stable"] or args.phase == "initial", **result}, sort_keys=True))
    return 0 if result["stable"] or args.phase == "initial" else 2


if __name__ == "__main__":
    raise SystemExit(main())
