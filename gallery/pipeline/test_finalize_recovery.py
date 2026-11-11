import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import faces
import finalize_recovery as finalizer
import obfuscate
from remote import LocalRemote, QuotaDeferred


class _NoBudget:
    def check(self, operation, units=1):
        if operation == "writes":
            raise QuotaDeferred(operation)

    def save(self):
        pass


class FinalizeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = "k" * 64
        self.backup = self.root / "backup"
        (self.backup / "data").mkdir(parents=True)
        people = self.backup / "people"
        (people / "reference_faces" / "students").mkdir(parents=True)
        (people / "identities.json").write_text(json.dumps({"students": [{"reg_no": "0001", "name": "Private Person"}], "staff": []}), encoding="utf-8")
        Image.new("RGB", (32, 32), "white").save(people / "reference_faces" / "students" / "0001.jpg")
        (self.backup / "img-key.txt").write_text(self.key, encoding="utf-8")
        self.remote = LocalRemote(self.root / "remote")

    def tearDown(self):
        self.temp.cleanup()

    def _seed_baseline(self):
        baseline = self.root / "seed-baseline"
        legacy_path = "legacy-alias.webp"
        avatar_path = obfuscate.hashed(self.key.encode(), "img/people/S0001.webp").removeprefix("img/")
        (baseline / Path(avatar_path).parent).mkdir(parents=True, exist_ok=True)
        (baseline / avatar_path).write_bytes(b"old deployed avatar")
        (baseline / legacy_path).write_bytes(b"old URL alias")
        self.remote.pack_put(finalizer.BASELINE_KEY, baseline)
        paths = sorted([avatar_path, legacy_path])
        self.remote.put_json(finalizer.BASELINE_META_KEY, {
            "sha256": finalizer._baseline_digest(baseline, paths),
            "paths": paths,
        })
        self.remote.put("state/catalog.json", b"private catalog sentinel")
        return baseline, avatar_path, legacy_path

    def test_publishes_only_canonical_avatar_and_seeds_exact_hash_manifest(self):
        _, avatar_path, legacy_path = self._seed_baseline()
        original_baseline = self.remote.get(finalizer.BASELINE_KEY)
        original_metadata = self.remote.get(finalizer.BASELINE_META_KEY)
        expected_gallery = self.root / "expected"
        expected_data = expected_gallery / "data"
        expected_data.mkdir(parents=True)
        old = (faces.GALLERY, faces.DATA, faces.HELLO)
        try:
            faces.GALLERY, faces.DATA, faces.HELLO = expected_gallery, expected_data, self.backup / "people"
            expected_people = faces.load_people()
            faces._people_outputs(expected_people)
        finally:
            faces.GALLERY, faces.DATA, faces.HELLO = old
        expected_avatar = (expected_gallery / "img" / "people" / "S0001.webp").read_bytes()

        result = finalizer.finalize(self.backup, self.remote, image_key=self.key)
        self.assertEqual(result, {"avatars": 1, "assets": 2})
        self.assertEqual(self.remote.get("state/catalog.json"), b"private catalog sentinel")
        self.assertEqual(self.remote.get(finalizer.BASELINE_KEY), original_baseline)
        self.assertEqual(self.remote.get(finalizer.BASELINE_META_KEY), original_metadata)
        self.assertEqual(self.remote.get(f"img/{avatar_path}"), expected_avatar)
        manifest = self.remote.get_json(finalizer.IMAGE_MANIFEST_KEY)
        self.assertEqual(manifest, {avatar_path: hashlib.sha1(expected_avatar).hexdigest()})
        self.assertNotIn("S0001", " ".join(manifest))
        writes_after_first = self.remote.stats["writes"]
        self.assertEqual(finalizer.finalize(self.backup, self.remote, image_key=self.key), {"avatars": 1, "assets": 2})
        self.assertEqual(self.remote.stats["writes"], writes_after_first)

        expected_data.mkdir(exist_ok=True)
        (expected_data / "people.json").write_text(json.dumps([{
            "id": "S0001", "avatar": "img/people/S0001.webp",
        }]), encoding="utf-8")
        source_site = self.root / "source-site"
        obfuscate.build(expected_gallery, source_site, self.key.encode())
        self.assertEqual(self.remote.push_dir(source_site / "img", "img/", only_changed=True), 0)

        Image.new("RGB", (32, 32), "black").save(self.backup / "people" / "reference_faces" / "students" / "0001.jpg")
        changed_gallery = self.root / "changed"
        changed_data = changed_gallery / "data"
        changed_data.mkdir(parents=True)
        old = (faces.GALLERY, faces.DATA, faces.HELLO)
        try:
            faces.GALLERY, faces.DATA, faces.HELLO = changed_gallery, changed_data, self.backup / "people"
            faces._people_outputs(faces.load_people())
        finally:
            faces.GALLERY, faces.DATA, faces.HELLO = old
        (changed_data / "people.json").write_text(json.dumps([{
            "id": "S0001", "avatar": "img/people/S0001.webp",
        }]), encoding="utf-8")
        changed_site = self.root / "changed-site"
        obfuscate.build(changed_gallery, changed_site, self.key.encode())
        changed_avatar_path = (changed_site / "data" / "people.json")
        obfuscated_path = json.loads(changed_avatar_path.read_text())[0]["avatar"].removeprefix("img/")
        changed_avatar = (changed_site / "img" / obfuscated_path).read_bytes()
        self.assertNotEqual(changed_avatar, expected_avatar)
        self.assertEqual(self.remote.push_dir(changed_site / "img", "img/", only_changed=True), 1)

    def test_refuses_quota_shortfall_before_writing_any_owned_key(self):
        self._seed_baseline()
        before = {key: self.remote.get(key) for key in (
            finalizer.BASELINE_KEY, finalizer.BASELINE_META_KEY, finalizer.IMAGE_MANIFEST_KEY,
            "state/catalog.json",
        )}
        self.remote.budget = _NoBudget()
        with self.assertRaises(QuotaDeferred):
            finalizer.finalize(self.backup, self.remote, image_key=self.key)
        for key, value in before.items():
            self.assertEqual(self.remote.get(key), value)

    def test_storage_preflight_counts_avatar_bytes_before_any_put(self):
        class FakeKV(LocalRemote):
            backend = "kv"

        store = FakeKV(self.root / "fake-kv")
        store.put("state/existing", b"x" * 15)
        upload = [("img/avatar.webp", b"y" * 6, "image/webp")]
        with patch.object(finalizer, "MAX_KV_STORAGE_BYTES", 20), \
                patch.object(finalizer, "KV_STORAGE_RESERVE_BYTES", 0):
            with self.assertRaises(finalizer.FinalizeError) as raised:
                finalizer._preflight_storage(store, upload)
        self.assertEqual(raised.exception.code, "kv_storage_preflight_failed")
        self.assertIsNone(store.get("img/avatar.webp"))

    def test_retry_skips_avatar_put_completed_before_manifest_failure(self):
        _, avatar_path, _ = self._seed_baseline()
        original_baseline = self.remote.get(finalizer.BASELINE_KEY)
        original_put_many = self.remote.put_many

        def put_then_fail(items):
            original_put_many(items)
            raise OSError("simulated manifest-stage failure")

        with patch.object(self.remote, "put_many", side_effect=put_then_fail):
            with self.assertRaises(OSError):
                finalizer.finalize(self.backup, self.remote, image_key=self.key)
        self.assertEqual(self.remote.get(finalizer.BASELINE_KEY), original_baseline)
        self.assertIsNone(self.remote.get(finalizer.IMAGE_MANIFEST_KEY))
        writes_after_avatar = self.remote.stats["writes"]

        result = finalizer.finalize(self.backup, self.remote, image_key=self.key)
        self.assertEqual(result, {"avatars": 1, "assets": 2})
        self.assertEqual(self.remote.stats["writes"] - writes_after_avatar, 1)
        manifest = self.remote.get_json(finalizer.IMAGE_MANIFEST_KEY)
        self.assertEqual(manifest[avatar_path], hashlib.sha1(self.remote.get(f"img/{avatar_path}")).hexdigest())

    def test_rejects_wrong_key_and_inconsistent_baseline_without_mutation(self):
        self._seed_baseline()
        original = self.remote.get(finalizer.BASELINE_KEY)
        with self.assertRaises(finalizer.FinalizeError):
            finalizer.finalize(self.backup, self.remote, image_key="x" * 64)
        self.assertEqual(self.remote.get(finalizer.BASELINE_KEY), original)

        self.remote.put_json(finalizer.BASELINE_META_KEY, {"sha256": "wrong", "paths": ["wrong.webp"]})
        corrupt = self.remote.get(finalizer.BASELINE_KEY)
        with self.assertRaises(finalizer.FinalizeError):
            finalizer.finalize(self.backup, self.remote, image_key=self.key)
        self.assertEqual(self.remote.get(finalizer.BASELINE_KEY), corrupt)
        self.assertIsNone(self.remote.get(finalizer.IMAGE_MANIFEST_KEY))

if __name__ == "__main__":
    unittest.main()
