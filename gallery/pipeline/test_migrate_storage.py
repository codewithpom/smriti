import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from remote import LocalRemote
import migrate_storage as migration


class ReadOnlySource(LocalRemote):
    def delete(self, keys):  # Any accidental KV/source deletion fails immediately.
        raise AssertionError("source delete must never be called")


class FailOnceDestination(LocalRemote):
    def __init__(self, root):
        super().__init__(root)
        self.failed = False

    def put(self, key, data, content_type=None):
        if key == "data/second.json" and not self.failed:
            self.failed = True
            raise OSError("injected destination interruption")
        return super().put(key, data, content_type)


class MutatingSource(ReadOnlySource):
    def __init__(self, root):
        super().__init__(root)
        self.mutations = 0

    def get(self, key):
        data = super().get(key)
        if key == "data/live.json":
            self.mutations += 1
            self.put(key, f"value-{self.mutations}".encode())
        return data


class ChangesOnceSource(ReadOnlySource):
    def __init__(self, root):
        super().__init__(root)
        self.changed = False

    def get(self, key):
        data = super().get(key)
        if key == "data/live.json" and not self.changed:
            self.changed = True
            self.put(key, b"new-value")
        return data


class MigrateStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = ReadOnlySource(self.root / "source")
        self.destination = LocalRemote(self.root / "destination")

    def tearDown(self):
        self.temp.cleanup()

    def run_migration(self, source=None, destination=None, **kwargs):
        return migration.migrate(
            source or self.source,
            destination or self.destination,
            phase=kwargs.pop("phase", "initial"),
            source_id=kwargs.pop("source_id", "source-test"),
            destination_id=kwargs.pop("destination_id", "bucket-test"),
            settle_seconds=kwargs.pop("settle_seconds", 0),
            **kwargs,
        )

    def test_delta_uses_configured_quiet_window_before_stability_check(self):
        self.source.put("data/live.json", b"stable")
        with patch.object(migration.time, "sleep") as sleep:
            result = self.run_migration(phase="delta", settle_seconds=75)
        self.assertTrue(result["stable"])
        self.assertEqual(result["quiet_window_seconds"], 75)
        sleep.assert_called_once_with(75)

    def test_initial_copies_all_logical_prefixes_and_delta_verifies_exact_bytes(self):
        values = {
            "pending/u1/upload.jpg": b"raw-upload",
            "review/corrections.json": b'{"faces":{}}',
            "state/fx.tar.gz": b"packed-cache",
            "state/published-assets.tar.gz": b"packed-images",
            "state/manifest/state.json": b"{}",
            "data/photos.json": b"[]",
            "img/web/hash.webp": b"web-image",
            "secrets/fb-cookies.txt": b"private-cookie-bytes",
            "people/S1.jpg": b"portrait",
            "originals/p1.jpg": b"original-photo",
        }
        for key, value in values.items():
            self.source.put(key, value)
        source_before = dict(self.source.ls(""))

        initial = self.run_migration(phase="initial")
        self.assertFalse(initial["stable"])
        self.assertEqual(dict(self.source.ls("")), source_before)
        self.assertEqual({key: self.destination.get(key) for key in values}, values)

        # Same-size corruption must be found by the hash comparison.
        self.destination.put("data/photos.json", b"{}")
        verified = self.run_migration(phase="delta")
        self.assertTrue(verified["stable"])
        self.assertEqual(self.destination.get("data/photos.json"), b"[]")
        self.assertEqual(self.destination.get("state/published-assets.tar.gz"), b"packed-images")
        manifest = json.loads(self.destination.get(migration.MANIFEST_KEY))
        self.assertTrue(manifest["stable"])
        self.assertEqual(set(manifest["owned_keys"]), set(values))

    def test_resume_after_partial_destination_write_uses_journal(self):
        source = self.source
        source.put("data/first.json", b"first")
        source.put("data/second.json", b"second")
        destination = FailOnceDestination(self.root / "fail-once-destination")
        with self.assertRaises(OSError):
            self.run_migration(source, destination)

        # Both batch keys were journaled before the first destination mutation.
        manifest = json.loads(destination.get(migration.MANIFEST_KEY))
        self.assertEqual(set(manifest["inflight_put"]), {"data/first.json", "data/second.json"})
        result = self.run_migration(source, destination)
        self.assertTrue(result["stable"] is False)  # initial phase is a single pass
        self.assertEqual(destination.get("data/first.json"), b"first")
        self.assertEqual(destination.get("data/second.json"), b"second")
        manifest = json.loads(destination.get(migration.MANIFEST_KEY))
        self.assertEqual(set(manifest["owned_keys"]), {"data/first.json", "data/second.json"})
        self.assertEqual(manifest["inflight_put"], [])

    def test_delta_deletes_only_owned_destination_keys_missing_from_stable_source(self):
        self.source.put("pending/upload.jpg", b"pending")
        self.source.put("review/one.json", b"{}")
        self.run_migration(phase="initial")
        self.source._path("pending/upload.jpg").unlink()

        result = self.run_migration(phase="delta")
        self.assertTrue(result["stable"])
        self.assertEqual(result["deleted_destination_keys"], 1)
        self.assertIsNone(self.destination.get("pending/upload.jpg"))
        self.assertEqual(self.destination.get("review/one.json"), b"{}")

    def test_delta_fails_closed_when_source_never_stabilizes(self):
        source = MutatingSource(self.root / "mutating-source")
        source.put("data/live.json", b"start")
        result = self.run_migration(source, phase="delta", max_passes=2)
        self.assertFalse(result["stable"])
        manifest = json.loads(self.destination.get(migration.MANIFEST_KEY))
        self.assertEqual(manifest["status"], "unstable")

    def test_delta_repeats_until_a_changed_source_value_is_copied_and_stable(self):
        source = ChangesOnceSource(self.root / "changes-once-source")
        source.put("data/live.json", b"old")
        result = self.run_migration(source, phase="delta", max_passes=3)
        self.assertTrue(result["stable"])
        self.assertEqual(result["source_change_detections"], 1)
        self.assertEqual(self.destination.get("data/live.json"), b"new-value")

    def test_nonempty_unowned_destination_is_rejected_before_any_copy(self):
        self.source.put("data/photos.json", b"[]")
        self.destination.put("unrelated/object", b"keep")
        writes_before = self.destination.stats["writes"]
        with self.assertRaisesRegex(migration.MigrationError, "destination_not_empty"):
            self.run_migration()
        self.assertEqual(self.destination.stats["writes"], writes_before)
        self.assertEqual(self.destination.get("unrelated/object"), b"keep")

    def test_reserved_manifest_key_collision_is_rejected_before_destination_write(self):
        self.source.put(migration.MANIFEST_KEY, b"source-data")
        writes_before = self.destination.stats["writes"]
        with self.assertRaisesRegex(migration.MigrationError, "reserved_source_key_collision"):
            self.run_migration()
        self.assertEqual(self.destination.stats["writes"], writes_before)

    def test_migration_identity_mismatch_rejects_resume(self):
        self.source.put("data/photos.json", b"[]")
        self.run_migration()
        with self.assertRaisesRegex(migration.MigrationError, "migration_manifest_identity_mismatch"):
            self.run_migration(source_id="other-source")

    def test_content_type_fallback_covers_pipeline_objects(self):
        self.assertEqual(migration._content_type("data/photos.json"), "application/json")
        self.assertEqual(migration._content_type("state/fx.tar.gz"), "application/gzip")
        self.assertEqual(migration._content_type("img/photo.webp"), "image/webp")
        self.assertEqual(migration._content_type("pending/photo.jpg"), "image/jpeg")
        self.assertEqual(migration._content_type("other/blob"), "application/octet-stream")

    def test_configured_source_view_exposes_no_mutation_methods(self):
        source = migration.ReadOnlySource(self.source)
        self.assertTrue(callable(source.get))
        self.assertTrue(callable(source.ls))
        self.assertFalse(hasattr(source, "put"))
        self.assertFalse(hasattr(source, "delete"))


if __name__ == "__main__":
    unittest.main()
