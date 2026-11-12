import hashlib
import json
from pathlib import Path
import copy
import tempfile
import unittest
from unittest import mock

from remote import LocalRemote
import repair_recovery_ci as repair


class RepairRecoveryCITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_state_file_selection_uses_existing_allowlist(self):
        data = self.root / "data"
        (data / "fx" / "r50f").mkdir(parents=True)
        (data / "extra_refs").mkdir()
        (data / "catalog.json").write_text("[]")
        (data / "extra_refs" / "ref.jpg").write_bytes(b"ref")
        (data / "fx" / "r50f" / "cache.json").write_text("[]")
        self.assertEqual(set(repair._state_files(data)), {"catalog.json", "extra_refs/ref.jpg"})

    def test_historical_snapshot_check_preserves_face_geometry_and_labels(self):
        original_photos = [{
            "id": "p1", "w": 100, "h": 80, "date": "2020-01-01",
            "faces": [
                {"i": 0, "bbox": [0.1, 0.2, 0.2, 0.3], "person": "S1", "status": "confirmed"},
                {"i": 1, "bbox": [0.5, 0.2, 0.2, 0.3], "person": None, "status": "rejected"},
            ],
        }]
        served_photos = [{
            **copy.deepcopy(original_photos[0]),
            "faces": copy.deepcopy(original_photos[0]["faces"]) + [
                {"i": 2, "bbox": [0.3, 0.5, 0.1, 0.1], "person": None, "status": "candidate"},
            ],
        }]
        people = [{"id": "S1", "name": "Example", "kind": "student"}]
        repair._assert_recovery_snapshot(original_photos, people, served_photos, people)

        served_photos[0]["faces"][0]["bbox"] = [0.11, 0.2, 0.2, 0.3]
        with self.assertRaisesRegex(repair.RepairError, "recovery_archive_face_position_mismatch"):
            repair._assert_recovery_snapshot(original_photos, people, served_photos, people)

    def test_historical_validation_allows_added_faces_but_preserves_known_labels(self):
        old_photos = [{
            "id": "p1", "w": 100, "h": 80, "date": "2020-01-01",
            "faces": [{"i": 0, "bbox": [0.1, 0.2, 0.2, 0.3], "person": "S1", "status": "high"}],
        }]
        new_photos = [{
            **old_photos[0],
            "faces": old_photos[0]["faces"] + [
                {"i": 1, "bbox": [0.6, 0.2, 0.1, 0.2], "person": None, "status": "candidate"},
            ],
        }]
        people = [{"id": "S1", "name": "Example", "kind": "student"}]
        counts = {
            "repaired_embeddings": 1,
            "remaining_missing_embeddings": 0,
            "removed_extra_detections": 1,
            "archived_faces": 1,
            "transferred_labels": 1,
            "unmatched_labels": 0,
        }
        repair._validate_historical(
            old_photos, new_photos, people, people,
            [{"photo": "p1", "faces": [{"embedding_missing": True}]}], counts,
        )

    def test_live_snapshot_accepts_exact_backup_and_desired_state_only(self):
        old_state = self.root / "old"
        desired_state = self.root / "desired"
        old_state.mkdir(); desired_state.mkdir()
        (old_state / "catalog.json").write_text('[{"id":"p1"}]')
        (old_state / "faces_raw.json").write_text("old")
        (desired_state / "catalog.json").write_text('[{"id":"p1"}]')
        (desired_state / "faces_raw.json").write_text("repaired")

        old_served = {name: [] for name in repair.SITE_JSON}
        desired_served = {name: [] for name in repair.SITE_JSON}
        remote = LocalRemote(self.root / "remote")
        for name in repair.SITE_JSON:
            remote.put_json(f"data/{name}.json", old_served[name])
        remote.put("state/catalog.json", (old_state / "catalog.json").read_bytes())
        remote.put("state/faces_raw.json", (old_state / "faces_raw.json").read_bytes())
        remote.put_json(repair.STATE_MANIFEST_KEY, {
            "catalog.json": hashlib.sha1((old_state / "catalog.json").read_bytes()).hexdigest(),
            "faces_raw.json": hashlib.sha1((old_state / "faces_raw.json").read_bytes()).hexdigest(),
        })

        hashes = repair._verify_live_snapshot(
            remote, repair._state_files(old_state), repair._state_files(desired_state),
            old_served, desired_served,
        )
        self.assertEqual(hashes["faces_raw.json"], repair._sha256_bytes(b"old"))
        remote.put("state/catalog.json", b"changed")
        with self.assertRaisesRegex(repair.RepairError, "live_state_snapshot_changed"):
            repair._verify_live_snapshot(
                remote, repair._state_files(old_state), repair._state_files(desired_state),
                old_served, desired_served,
            )

    def test_resume_snapshot_accepts_recorded_source_hashes(self):
        repaired = self.root / "repaired"
        repaired.mkdir()
        (repaired / "catalog.json").write_text("desired")
        state = repair._state_files(repaired)
        remote = LocalRemote(self.root / "resume-remote")
        source_payload = b"source"
        remote.put("state/catalog.json", source_payload)
        source_served = {name: [{"source": name}] for name in repair.SITE_JSON}
        desired_served = {name: [{"desired": name}] for name in repair.SITE_JSON}
        for name in repair.SITE_JSON:
            remote.put_json(f"data/{name}.json", source_served[name])
        remote.put_json(repair.STATE_MANIFEST_KEY, {"catalog.json": hashlib.sha1(source_payload).hexdigest()})
        hashes = repair._verify_live_snapshot(
            remote, state, state, desired_served, desired_served,
            source_state_hashes={"catalog.json": repair._sha256_bytes(source_payload)},
            source_state_sha1={"catalog.json": hashlib.sha1(source_payload).hexdigest()},
            source_served_hashes={name: repair._json_hash(source_served[name]) for name in repair.SITE_JSON},
        )
        self.assertEqual(hashes["catalog.json"], repair._sha256_bytes(source_payload))

    def test_prepared_backup_reuse_validates_desired_files(self):
        backup = self.root / "prepared"
        (backup / "data").mkdir(parents=True)
        (backup / "site-data").mkdir()
        (backup / "repairs").mkdir()
        (backup / "data" / "catalog.json").write_text("state")
        served_hashes = {}
        for name in repair.SITE_JSON:
            payload = [{"name": name}]
            path = backup / "site-data" / f"{name}.json"
            path.write_text(json.dumps(payload))
            served_hashes[name] = repair._json_hash(payload)
        manifest = {
            "status": "prepared_cloud_apply_pending",
            "counts": {"repaired_embeddings": 1},
            "desired_state_sha256": {"catalog.json": repair._sha256_file(backup / "data" / "catalog.json")},
            "desired_served_sha256": served_hashes,
        }
        (backup / "repairs" / repair.TRANSACTION_FILE).write_text(json.dumps(manifest))
        self.assertEqual(repair._validate_prepared_backup(backup), {"repaired_embeddings": 1})
        (backup / "site-data" / "photos.json").write_text("[]")
        with self.assertRaisesRegex(repair.RepairError, "prepared_recovery_gallery_changed"):
            repair._validate_prepared_backup(backup)

    def test_image_reference_parser_rejects_traversal(self):
        self.assertEqual(repair._public_assets({"web": "img/abcdef.webp"}), {"abcdef.webp"})
        with self.assertRaisesRegex(repair.RepairError, "public_image_reference_invalid"):
            repair._public_assets({"web": "img/../private.webp"})

    def test_public_image_urls_must_match_for_no_static_deploy(self):
        photos = [{"id": "p1", "web": "img/web/p1.webp", "thumb": "img/thumb/p1.webp"}]
        people = [{"id": "S1", "avatar": "img/people/S1.webp"}]
        repair._assert_public_image_urls(photos, copy.deepcopy(photos), people, copy.deepcopy(people))
        changed = copy.deepcopy(photos)
        changed[0]["thumb"] = "img/thumb/new.webp"
        with self.assertRaisesRegex(repair.RepairError, "public_photo_urls_changed"):
            repair._assert_public_image_urls(photos, changed, people, people)
        changed_people = copy.deepcopy(people)
        changed_people[0]["avatar"] = "img/people/new.webp"
        with self.assertRaisesRegex(repair.RepairError, "public_avatar_urls_changed"):
            repair._assert_public_image_urls(photos, photos, people, changed_people)

    def test_prepare_copies_backup_and_never_opens_remote(self):
        original = self.root / "original"
        output = self.root / "prepared"
        original.mkdir()
        marker = original / "unchanged.txt"
        marker.write_text("original")
        with mock.patch.object(repair, "_repair", side_effect=lambda out, archive, **kwargs:
                          (out / "prepared.txt").write_text("repaired") or {"repaired_embeddings": 1}), \
             mock.patch.object(repair, "open_remote") as open_remote:
            self.assertEqual(repair.main(["prepare", str(original), str(output), str(self.root)]), 0)
        self.assertEqual(marker.read_text(), "original")
        self.assertTrue((output / "prepared.txt").is_file())
        open_remote.assert_not_called()

    def test_remap_handles_obfuscator_writing_directly_to_requested_output(self):
        data = self.root / "gallery" / "data"
        output = self.root / "backup" / "repairs" / "data"
        data.mkdir(parents=True)

        def write_generated(_gallery, destination, _key, require_files=False):
            generated = destination / "data"
            generated.mkdir(parents=True, exist_ok=True)
            for name in (*repair.SITE_JSON, "pending"):
                (generated / f"{name}.json").write_text("[]")

        with mock.patch.object(repair.faces, "match"), \
             mock.patch.object(repair.build, "main"), \
             mock.patch.object(repair.obfuscate, "build", side_effect=write_generated):
            files = repair._remap_gallery_data(data, output, "k" * 32)

        self.assertEqual(set(files), set(repair.SITE_JSON))
        self.assertTrue(all(path.is_file() for path in files.values()))
        self.assertEqual((output / "pending.json").read_text(), "[]")

    def test_activation_is_last_and_not_attempted_after_state_write_failure(self):
        class RecordingRemote:
            def __init__(self, fail=False):
                self.calls = []
                self.fail = fail
            def put_many(self, items):
                self.calls.append(("put_many", [item[0] for item in items]))
                if self.fail and items[0][0].startswith("state/"):
                    raise OSError("write failed")
            def put(self, key, payload, content_type):
                self.calls.append(("put", key))
            def delete(self, keys):
                self.calls.append(("delete", keys))

        activation = [("data/photos.json", b"[]", "application/json")]
        remote = RecordingRemote()
        repair._apply_writes(remote, [("state/faces.json", b"{}", None)], [], b"{}", activation)
        self.assertEqual(remote.calls[-1], ("put_many", ["data/photos.json"]))
        failed = RecordingRemote(fail=True)
        with self.assertRaises(OSError):
            repair._apply_writes(failed, [("state/faces.json", b"{}", None)], [], b"{}", activation)
        self.assertFalse(any("data/photos.json" in call[1] if isinstance(call[1], list) else False
                             for call in failed.calls))


if __name__ == "__main__":
    unittest.main()
