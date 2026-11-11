"""Canonicalize recovered public avatars and seed the image delta manifest.

This is a light follow-up to recover_ci.py: it does not run face detection,
embedding, or matching. It leaves the packed published image baseline intact,
uploads canonical avatars individually, and updates the ``img/`` push manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import faces
import obfuscate
from remote import CHUNK_SIZE, open_remote


BASELINE_META_KEY = "state/published-assets.json"
BASELINE_KEY = "state/published-assets.tar.gz"
IMAGE_MANIFEST_KEY = "state/manifest/img.json"
ACTIVATION_WRITE_RESERVE = 5
MAX_KV_STORAGE_BYTES = 1 << 30
KV_STORAGE_RESERVE_BYTES = 32 * 1024 * 1024


class FinalizeError(Exception):
    """Recovered image state is incomplete or inconsistent.

    ``code`` is deliberately a short, non-sensitive diagnostic suitable for
    public workflow logs. Never attach archive paths, image names, or values.
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _baseline_digest(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in paths:
        digest.update(rel.encode())
        digest.update(hashlib.sha256((root / rel).read_bytes()).digest())
    return digest.hexdigest()


def _safe_manifest_paths(values: object) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise FinalizeError("image_manifest_invalid")
    result = []
    for value in values:
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
                or not path.parts or value != "/".join(path.parts)):
            raise FinalizeError("image_manifest_invalid")
        result.append(value)
    if result != sorted(set(result)):
        raise FinalizeError("image_manifest_invalid")
    return result


def _canonical_avatars(backup_dir: Path, temp_root: Path) -> tuple[Path, list[Path]]:
    people_dir, source_data = backup_dir / "people", backup_dir / "data"
    if not (people_dir / "identities.json").is_file():
        raise FinalizeError("recovery_roster_missing")
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


def _preflight_writes(remote, writes: int, *, reserve: int = 0) -> None:
    budget = getattr(remote, "budget", None)
    if budget is None:
        return
    budget.check("writes", writes + reserve)


def _preflight_storage(remote, uploads: list[tuple[str, bytes, str]]) -> None:
    if getattr(remote, "backend", None) != "kv":
        return
    used = sum(size for _, size in remote.ls(""))
    # Count each avatar as additional storage even if an existing object may
    # be replaced, so the check remains conservative.
    projected = used + sum(len(data) for _, data, _ in uploads)
    if projected + KV_STORAGE_RESERVE_BYTES > MAX_KV_STORAGE_BYTES:
        raise FinalizeError("kv_storage_preflight_failed")


def finalize(backup_dir: Path | str, remote=None, *, image_key: str | None = None) -> dict[str, int]:
    """Upload canonical avatars and record their exact image hashes."""
    backup_dir = Path(backup_dir)
    image_key = image_key if image_key is not None else os.environ.get("IMG_KEY", "")
    recovered_key_path = backup_dir / "img-key.txt"
    if len(image_key) < 32 or not recovered_key_path.is_file():
        raise FinalizeError("recovery_image_key_missing")
    recovered_key = recovered_key_path.read_text(encoding="utf-8").strip()
    if recovered_key != image_key:
        raise FinalizeError("recovery_image_key_mismatch")

    remote = remote or open_remote()
    try:
        metadata = remote.get_json(BASELINE_META_KEY)
        if not isinstance(metadata, dict):
            raise FinalizeError("image_baseline_metadata_missing")
        expected_paths = _safe_manifest_paths(metadata.get("paths"))
        digest = metadata.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise FinalizeError("image_digest_invalid")
        with tempfile.TemporaryDirectory(prefix="smriti-finalize-recovery-") as temporary:
            root = Path(temporary)
            _, avatars = _canonical_avatars(backup_dir, root)
            if not avatars:
                raise FinalizeError("canonical_avatars_missing")
            uploads = []
            avatar_hashes = {}
            for avatar in avatars:
                rel = f"img/people/{avatar.name}"
                hashed = obfuscate.hashed(image_key.encode("utf-8"), rel)
                target_rel = hashed.removeprefix("img/")
                if target_rel not in expected_paths:
                    raise FinalizeError("canonical_avatar_not_in_baseline")
                canonical = avatar.read_bytes()
                if len(canonical) > CHUNK_SIZE:
                    raise FinalizeError("canonical_avatar_too_large")
                avatar_hashes[target_rel] = hashlib.sha1(canonical).hexdigest()
                current = remote.get(f"img/{target_rel}")
                if current != canonical:
                    uploads.append((f"img/{target_rel}", canonical, "image/webp"))

            current_manifest = remote.get_json(IMAGE_MANIFEST_KEY, {})
            if not isinstance(current_manifest, dict):
                raise FinalizeError("image_manifest_invalid")
            image_manifest = {**current_manifest, **avatar_hashes}
            manifest_changed = image_manifest != current_manifest
            # Reserve the five small JSON writes performed by the following
            # activation step, so the bundle cannot fit preflight but exhaust
            # the shared daily budget before it becomes usable.
            _preflight_writes(remote, len(uploads) + int(manifest_changed), reserve=ACTIVATION_WRITE_RESERVE)
            _preflight_storage(remote, uploads)

            # Check the exact remote bytes first: if a prior run failed partway
            # through this bulk upload, retries skip already-correct avatars.
            remote.put_many(uploads)
            for rel, expected_hash in avatar_hashes.items():
                uploaded = remote.get(f"img/{rel}")
                if uploaded is None or hashlib.sha1(uploaded).hexdigest() != expected_hash:
                    raise FinalizeError("canonical_avatar_upload_verification_failed")
            if manifest_changed:
                remote.put_json(IMAGE_MANIFEST_KEY, image_manifest)
            return {"avatars": len(avatars), "assets": len(expected_paths)}
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
        diagnostic = error.code if isinstance(error, FinalizeError) else type(error).__name__
        print(f"recovery finalization failed: {diagnostic}", file=sys.stderr)
        return 1
    print(f"recovery image baseline finalized: avatars={counts['avatars']} assets={counts['assets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
