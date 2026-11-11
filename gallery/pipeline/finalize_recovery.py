"""Canonicalize recovered public avatars and seed the image delta manifest.

This is a light follow-up to recover_ci.py: it does not run face detection,
embedding, or matching. It updates only the published image baseline and the
two metadata keys that describe that baseline and the ``img/`` push manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import faces
import obfuscate
from remote import CHUNK_SIZE, open_remote


BASELINE_KEY = "state/published-assets.tar.gz"
BASELINE_META_KEY = "state/published-assets.json"
IMAGE_MANIFEST_KEY = "state/manifest/img.json"
VERSIONED_BASELINE_PREFIX = "state/published-assets/"
MAX_KV_STORAGE_BYTES = 1 << 30
KV_STORAGE_RESERVE_BYTES = 32 * 1024 * 1024


class FinalizeError(Exception):
    """Recovered image state is incomplete or inconsistent."""


def _paths(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _baseline_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in paths:
        digest.update(rel.encode())
        digest.update(hashlib.sha256((root / rel).read_bytes()).digest())
    return digest.hexdigest()


def _safe_manifest_paths(values: object) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise FinalizeError("invalid published image manifest")
    result = []
    for value in values:
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
                or not path.parts or value != "/".join(path.parts)):
            raise FinalizeError("invalid published image manifest")
        result.append(value)
    if result != sorted(set(result)):
        raise FinalizeError("invalid published image manifest")
    return result


def _canonical_avatars(backup_dir: Path, temp_root: Path) -> tuple[Path, list[Path]]:
    people_dir, source_data = backup_dir / "people", backup_dir / "data"
    if not (people_dir / "identities.json").is_file():
        raise FinalizeError("recovery backup is missing its roster")
    gallery = temp_root / "gallery"
    data = gallery / "data"
    data.mkdir(parents=True)
    # load_people reads these private additions and aliases. Keep all output in
    # the disposable gallery tree; the recovered state directory stays intact.
    for name in ("extra_staff.json", "extra_students.json", "extra_students_noreg.json", "manual_aliases.json"):
        source = source_data / name
        if source.is_file():
            shutil.copy2(source, data / name)
    refs = source_data / "extra_refs"
    if refs.is_dir():
        shutil.copytree(refs, data / "extra_refs")

    old = (faces.GALLERY, faces.DATA, faces.HELLO)
    try:
        faces.GALLERY, faces.DATA, faces.HELLO = gallery, data, people_dir
        people = faces.load_people()
        faces._people_outputs(people)
    finally:
        faces.GALLERY, faces.DATA, faces.HELLO = old
    output = gallery / "img" / "people"
    if not output.is_dir():
        return output, []
    return output, sorted(path for path in output.glob("*.webp") if path.is_file())


def _put_write_units(size: int) -> int:
    if size <= CHUNK_SIZE:
        return 1
    return math.ceil(size / CHUNK_SIZE) + 1  # data chunks plus the KV chunk marker


def _preflight_writes(remote, sizes: list[int]) -> None:
    budget = getattr(remote, "budget", None)
    if budget is None:
        return
    writes = sum(_put_write_units(size) for size in sizes)
    budget.check("writes", writes)


def _preflight_storage(remote, object_key: str, archive_size: int) -> None:
    if getattr(remote, "backend", None) != "kv":
        return
    existing = dict(remote.ls(""))
    used = sum(existing.values())
    projected = used - existing.get(object_key, 0) + archive_size
    if projected + KV_STORAGE_RESERVE_BYTES > MAX_KV_STORAGE_BYTES:
        raise FinalizeError("insufficient KV storage for a versioned image baseline")


def _baseline_object(metadata: dict) -> str:
    """Resolve a trusted versioned pointer, retaining the legacy fixed-key fallback."""
    digest = metadata.get("sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise FinalizeError("invalid published image digest")
    value = metadata.get("object")
    if value is None:
        return BASELINE_KEY
    expected = f"{VERSIONED_BASELINE_PREFIX}{digest}.tar.gz"
    if value != expected:
        raise FinalizeError("invalid published image object pointer")
    return value


def _verified_archive(remote, key: str, destination: Path, paths: list[str], digest: str) -> bool:
    shutil.rmtree(destination, ignore_errors=True)
    try:
        if not remote.unpack_get(key, destination):
            return False
        actual_paths = _paths(destination)
        return actual_paths == paths and _baseline_digest(destination, actual_paths) == digest
    except Exception:
        return False


def _write_tar(root: Path, output) -> int:
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    output.seek(0, os.SEEK_END)
    size = output.tell()
    output.seek(0)
    return size


def finalize(backup_dir: Path | str, remote=None, *, image_key: str | None = None) -> dict[str, int]:
    """Update the packed public image baseline and its exact delta manifest."""
    backup_dir = Path(backup_dir)
    image_key = image_key if image_key is not None else os.environ.get("IMG_KEY", "")
    recovered_key_path = backup_dir / "img-key.txt"
    if len(image_key) < 32 or not recovered_key_path.is_file():
        raise FinalizeError("recovery image key is unavailable")
    recovered_key = recovered_key_path.read_text(encoding="utf-8").strip()
    if recovered_key != image_key:
        raise FinalizeError("recovery image key does not match the deployed key")

    remote = remote or open_remote()
    try:
        metadata = remote.get_json(BASELINE_META_KEY)
        if not isinstance(metadata, dict):
            raise FinalizeError("published image baseline metadata is missing")
        expected_paths = _safe_manifest_paths(metadata.get("paths"))
        source_key = _baseline_object(metadata)
        with tempfile.TemporaryDirectory(prefix="smriti-finalize-recovery-") as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            if not remote.unpack_get(source_key, baseline):
                raise FinalizeError("published image baseline is missing")
            paths = _paths(baseline)
            if paths != expected_paths or metadata.get("sha256") != _baseline_digest(baseline, paths):
                raise FinalizeError("published image baseline does not match its metadata")

            _, avatars = _canonical_avatars(backup_dir, root)
            if not avatars:
                raise FinalizeError("no canonical avatars were generated")
            replaced = 0
            baseline_changed = False
            for avatar in avatars:
                rel = f"img/people/{avatar.name}"
                hashed = obfuscate.hashed(image_key.encode("utf-8"), rel)
                target_rel = hashed.removeprefix("img/")
                if target_rel not in expected_paths:
                    raise FinalizeError("canonical avatar is absent from the published baseline")
                target = baseline / target_rel
                canonical = avatar.read_bytes()
                if canonical != target.read_bytes():
                    shutil.copyfile(avatar, target)
                    baseline_changed = True
                replaced += 1

            paths = _paths(baseline)
            digest = _baseline_digest(baseline, paths)
            object_key = source_key if not baseline_changed else f"{VERSIONED_BASELINE_PREFIX}{digest}.tar.gz"
            new_metadata = {"sha256": digest, "paths": paths}
            if object_key != BASELINE_KEY:
                new_metadata["object"] = object_key
            image_manifest = {rel: hashlib.sha1((baseline / rel).read_bytes()).hexdigest() for rel in paths}
            metadata_size = len(json.dumps(new_metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            manifest_size = len(json.dumps(image_manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            metadata_changed = new_metadata != metadata
            manifest_changed = image_manifest != remote.get_json(IMAGE_MANIFEST_KEY)

            with tempfile.TemporaryFile() as archive:
                sizes = []
                payload = None
                needs_archive_write = False
                if baseline_changed:
                    needs_archive_write = not _verified_archive(
                        remote, object_key, root / "verified-existing", paths, digest)
                    if needs_archive_write:
                        archive_size = _write_tar(baseline, archive)
                        payload = archive.read()
                        sizes.append(archive_size)
                if metadata_changed:
                    sizes.append(metadata_size)
                if manifest_changed:
                    sizes.append(manifest_size)
                _preflight_writes(remote, sizes)
                if needs_archive_write:
                    _preflight_storage(remote, object_key, archive_size)
            # Do not alter catalog, face state, or private people state.
            if needs_archive_write:
                remote.put(object_key, payload, "application/gzip")
                if not _verified_archive(remote, object_key, root / "verified-upload", paths, digest):
                    raise FinalizeError("uploaded image baseline failed verification")
            if metadata_changed:
                remote.put_json(BASELINE_META_KEY, new_metadata)
            if manifest_changed:
                remote.put_json(IMAGE_MANIFEST_KEY, image_manifest)
            return {"avatars": replaced, "assets": len(paths)}
    finally:
        budget = getattr(remote, "budget", None)
        if budget:
            budget.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_dir", type=Path, help="decrypted recovery backup directory")
    args = parser.parse_args(argv)
    try:
        counts = finalize(args.backup_dir)
    except Exception as error:
        # This workflow runs on a public repository. Never echo paths, IDs, or secrets.
        print(f"recovery finalization failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"recovery image baseline finalized: avatars={counts['avatars']} assets={counts['assets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
