import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import recover_archive


def write_image(path, color, size=(48, 32)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


class RecoverArchiveTests(unittest.TestCase):
    def test_reconstruct_prefers_matching_original_and_restores_ids_assets_and_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            data, assets, source = root / "data", root / "img", root / "source"
            originals = root / "originals"
            original_path = originals / "misc" / "keep-name.webp"
            write_image(original_path, (100, 0, 0), (64, 40))
            pid = recover_archive._photo_id_for_name(original_path.name)

            web_rel, thumb_rel, avatar_rel = "img/web/opaque.webp", "img/thumb/opaque.webp", "img/people/S7.webp"
            (archive / web_rel).parent.mkdir(parents=True, exist_ok=True)
            (archive / web_rel).write_bytes(original_path.read_bytes())
            write_image(archive / thumb_rel, (0, 100, 0), (64, 40))
            write_image(archive / avatar_rel, (0, 0, 100))
            (archive / "photos.json").write_text(json.dumps([{
                "id": pid, "date": "2026-01-02", "folder_scene": "classroom", "scene": "library",
                "scene_confirmed": True, "w": 64, "h": 40, "color": "#010203", "tw": 48, "th": 30,
                "activity": ["study"], "objects": [], "mood": [], "crowd": 2, "text": [], "alt": "",
                "thumb": thumb_rel, "web": web_rel, "faces": [],
            }]), encoding="utf-8")
            (archive / "people.json").write_text(json.dumps([{"id": "S7", "avatar": avatar_rel}]), encoding="utf-8")

            counts = recover_archive.reconstruct(archive, data, assets, source, [originals])
            catalog = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
            corrections = json.loads((data / "corrections.json").read_text(encoding="utf-8"))
            tags = json.loads((data / "tags_shard_recovered.json").read_text(encoding="utf-8"))

            self.assertEqual(catalog[0]["id"], pid)
            self.assertEqual(catalog[0]["file"], f"classroom/{original_path.name}")
            self.assertEqual((source / catalog[0]["file"]).read_bytes(), original_path.read_bytes())
            self.assertEqual(counts["matched_originals"], 1)
            self.assertEqual(corrections["scenes"], {pid: "library"})
            self.assertEqual(tags[pid]["activity"], ["study"])
            self.assertTrue((assets / "web" / f"{pid}.webp").is_file())
            self.assertTrue((assets / "thumb" / f"{pid}.webp").is_file())
            self.assertTrue((assets / "people" / "S7.webp").is_file())

    def test_reconstruct_uses_deployed_image_when_original_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            write_image(archive / "img/web/opaque.webp", (10, 20, 30))
            write_image(archive / "img/thumb/opaque.webp", (20, 30, 40))
            (archive / "photos.json").write_text(json.dumps([{
                "id": "p00000000", "folder_scene": "classroom", "w": 48, "h": 32,
                "web": "img/web/opaque.webp", "thumb": "img/thumb/opaque.webp", "faces": [],
            }]), encoding="utf-8")
            (archive / "people.json").write_text("[]", encoding="utf-8")

            counts = recover_archive.reconstruct(archive, root / "data", root / "img-out", root / "source")
            entry = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(entry["file"], "classroom/p00000000.webp")
            self.assertEqual(counts["archive_sources"], 1)
            self.assertTrue((root / "source" / entry["file"]).is_file())

    def test_rejects_traversal_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                recover_archive._safe_archive_image(Path(tmp), "img/../secrets.json")
            with self.assertRaises(ValueError):
                recover_archive._safe_archive_image(Path(tmp), r"img\..\secrets.json")

    def test_archive_ids_are_validated_before_output_paths_are_used(self):
        with self.assertRaises(ValueError):
            recover_archive._validate_archive([{"id": "../../x"}], [])
        with self.assertRaises(ValueError):
            recover_archive._validate_archive([], [{"id": "../../x"}])

    def test_reconstruct_people_restores_full_roster_and_prefers_local_portraits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, local = root / "archive", root / "local"
            people_dir, data_dir, asset_dir = root / "people", root / "data", root / "img"
            local_refs = local / "reference_faces"
            local_student = local_refs / "students" / "100.jpg"
            local_staff = local_refs / "staff" / "STAFF_1_original.jpg"
            write_image(local_student, (10, 20, 30))
            write_image(local_staff, (40, 50, 60))
            (local / "identities.json").write_text(json.dumps({
                "students": [{"reg_no": "100", "name": "Local Student", "section": "A", "percent": 99}],
                "staff": [{"id": "STAFF_1", "name": "Local Staff", "role": "Teacher", "img_url": "private"}],
            }), encoding="utf-8")

            deployed = [
                {"id": "S100", "kind": "student", "name": "Student One", "reg_no": "100", "batch": "MB27", "section": "A", "avatar": "img/people/S100.webp"},
                {"id": "S200", "kind": "student", "name": "Student Two", "reg_no": "200", "batch": "MB28", "section": "B", "avatar": "img/people/S200.webp"},
                {"id": "STAFF_1", "kind": "staff", "name": "Staff One", "role": "Teacher", "avatar": "img/people/STAFF_1.webp"},
                {"id": "STAFF_2", "kind": "staff", "name": "Staff Two", "role": "Office", "avatar": "img/people/STAFF_2.webp"},
            ]
            for person in deployed:
                write_image(archive / person["avatar"], (180, 0, 0))
            (archive / "people.json").write_text(json.dumps(deployed), encoding="utf-8")

            counts = recover_archive.reconstruct_people(archive, people_dir, local, data_dir, asset_dir)
            identities = json.loads((people_dir / "identities.json").read_text(encoding="utf-8"))
            extras = json.loads((data_dir / "extra_students.json").read_text(encoding="utf-8"))
            staff_extras = json.loads((data_dir / "extra_staff.json").read_text(encoding="utf-8"))
            meta = json.loads((data_dir / "people_meta.json").read_text(encoding="utf-8"))

            self.assertEqual(len(identities["students"]), 2)
            self.assertEqual(len(identities["staff"]), 2)
            self.assertEqual(len(extras), 2)
            self.assertEqual([person["id"] for person in staff_extras], ["STAFF_2"])
            self.assertEqual(len(meta), 4)
            self.assertNotIn("percent", identities["students"][0])
            self.assertNotIn("img_url", identities["staff"][0])
            self.assertEqual((people_dir / "reference_faces/students/100.jpg").read_bytes(), local_student.read_bytes())
            self.assertTrue((people_dir / "reference_faces/students/200.jpg").is_file())
            self.assertTrue((people_dir / "reference_faces/staff/STAFF_2_recovered.jpg").is_file())
            self.assertEqual(counts["deployed_people"], 4)

    def test_transfers_confirmed_labels_by_bbox_when_detector_order_changes(self):
        deployed = [{"id": "p1", "faces": [
            {"i": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "person": "S123", "status": "confirmed"},
            {"i": 1, "bbox": [0.6, 0.1, 0.2, 0.2], "person": None, "status": "rejected"},
            {"i": 2, "bbox": [0.3, 0.5, 0.2, 0.2], "person": None, "status": "candidate"},
        ]}]
        raw = [{"photo": "p1", "faces": [
            {"bbox": [0.6, 0.1, 0.2, 0.2]},
            {"bbox": [0.1, 0.1, 0.2, 0.2]},
        ]}]
        fixes, unresolved = recover_archive.transfer_confirmed_labels(deployed, raw)
        self.assertEqual(fixes, {"p1:1": "S123", "p1:0": "none"})
        self.assertEqual(unresolved, [])

    def test_restore_face_layout_keeps_historical_indices_and_marks_detector_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            old_faces = [
                {"i": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "person": "S100", "status": "confirmed"},
                {"i": 1, "bbox": [0.6, 0.1, 0.2, 0.2], "person": None, "status": "rejected"},
            ]
            photo = {"id": "p00000001", "w": 1000, "h": 500, "faces": old_faces}
            (data / "catalog.json").write_text(json.dumps([{"id": photo["id"], "width": 1000, "height": 500}]), encoding="utf-8")
            cache = data / "fx" / "r50f"
            cache.mkdir(parents=True)
            cache_face = {"b": [600, 50, 800, 150], "k": [[700, 100]] * 5, "w": 1000, "h": 500,
                          "d": 0.99, "q": 0.8, "v": [1.0] + [0.0] * 511}
            (cache / f"{photo['id']}.json").write_text(json.dumps([cache_face]), encoding="utf-8")
            raw = [{"photo": photo["id"], "faces": [{"bbox": old_faces[1]["bbox"], "det": 0.99, "px": 100,
                    "q": 0.8, "e": 0}]}]
            (data / "faces_raw.json").write_text(json.dumps(raw), encoding="utf-8")
            vector = np.zeros((1, 512), dtype=np.float32)
            vector[0, 0] = 1.0
            np.save(data / "face_emb.npy", vector)

            first = recover_archive.restore_face_layout(data, [photo], model_tag="r50f")
            rows = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))
            embeddings = np.load(data / "face_emb.npy", allow_pickle=False)
            cache_rows = json.loads((cache / f"{photo['id']}.json").read_text(encoding="utf-8"))
            corrections = json.loads((data / "corrections.json").read_text(encoding="utf-8"))

            self.assertEqual(first["archived_faces"], 2)
            self.assertEqual(first["missing_embeddings"], 1)
            self.assertEqual([f["bbox"] for f in rows[0]["faces"]], [f["bbox"] for f in old_faces])
            self.assertTrue(rows[0]["faces"][0]["embedding_missing"])
            self.assertFalse(rows[0]["faces"][1].get("embedding_missing", False))
            self.assertTrue(np.allclose(embeddings[0], np.zeros(512)))
            self.assertTrue(np.allclose(embeddings[1], vector[0]))
            self.assertTrue(cache_rows[0]["embedding_missing"])
            self.assertEqual(corrections["faces"], {f"{photo['id']}:0": "S100", f"{photo['id']}:1": "none"})

            again = recover_archive.restore_face_layout(data, [photo], model_tag="r50f")
            self.assertEqual(again["missing_embeddings"], 1)

            import faces
            source = data / "source"
            (source / "classroom").mkdir(parents=True)
            write_image(source / "classroom" / "p00000001.webp", (10, 20, 30))
            old_state = (faces.DATA, faces.SRC, faces.GALLERY, faces.MODEL_TAG)
            try:
                faces.DATA, faces.SRC = data, source
                faces.GALLERY, faces.MODEL_TAG = data.parent / "gallery", "r50f"
                with patch.object(faces, "load_people", return_value={}), \
                     patch.object(faces, "_references", return_value=(0, 0, 0)), \
                     patch.object(faces, "_people_outputs"):
                    faces.extract()
            finally:
                faces.DATA, faces.SRC, faces.GALLERY, faces.MODEL_TAG = old_state
            replayed = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))[0]["faces"]
            self.assertEqual([f["bbox"] for f in replayed], [f["bbox"] for f in old_faces])
            self.assertTrue(replayed[0]["embedding_missing"])

    def test_restore_repairs_unambiguous_high_confidence_cached_detection_without_model(self):
        import faces

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            pid = "p00000002"
            old_faces = [
                {"i": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "person": "S100", "status": "confirmed"},
                {"i": 1, "bbox": [0.6, 0.1, 0.2, 0.2], "person": None, "status": "rejected"},
            ]
            photo = {"id": pid, "w": 1000, "h": 500, "faces": old_faces}
            (data / "catalog.json").write_text(json.dumps([{"id": pid, "width": 1000, "height": 500}]), encoding="utf-8")
            cache = data / "fx" / "r50f"
            cache.mkdir(parents=True)
            # IoU with old face 0 is about .86: above the conservative recovery
            # floor, but below the former .90 cutoff.
            repaired_cache = {"b": [115, 50, 315, 150], "k": [[0, 0]] * 5, "w": 1000, "h": 500,
                              "d": 0.96, "q": 0.71, "v": [0.0, 1.0] + [0.0] * 510}
            matched_cache = {"b": [600, 50, 800, 150], "k": [[0, 0]] * 5, "w": 1000, "h": 500,
                             "d": 0.99, "q": 0.8, "v": [1.0] + [0.0] * 511}
            (cache / f"{pid}.json").write_text(json.dumps([repaired_cache, matched_cache]), encoding="utf-8")
            raw = [{"photo": pid, "faces": [
                {"bbox": [0.115, 0.1, 0.2, 0.2], "det": 0.96, "px": 100, "q": 0.71, "e": 0},
                {"bbox": old_faces[1]["bbox"], "det": 0.99, "px": 100, "q": 0.8, "e": 1},
            ]}]
            (data / "faces_raw.json").write_text(json.dumps(raw), encoding="utf-8")
            vectors = np.zeros((2, 512), dtype=np.float32)
            vectors[0, 1] = 1.0
            vectors[1, 0] = 1.0
            np.save(data / "face_emb.npy", vectors)

            counts = recover_archive.restore_face_layout(data, [photo], model_tag="r50f")
            rows = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))[0]["faces"]
            embeddings = np.load(data / "face_emb.npy", allow_pickle=False)
            cache_rows = json.loads((cache / f"{pid}.json").read_text(encoding="utf-8"))
            corrections = json.loads((data / "corrections.json").read_text(encoding="utf-8"))

            self.assertEqual(counts["repaired_embeddings"], 1)
            self.assertEqual(counts["missing_embeddings"], 0)
            self.assertEqual(counts["removed_extra_detections"], 1)
            self.assertEqual([f["bbox"] for f in rows], [f["bbox"] for f in old_faces])
            self.assertFalse(any(f.get("embedding_missing") for f in rows))
            self.assertTrue(np.array_equal(embeddings[0], vectors[0]))
            self.assertTrue(np.array_equal(embeddings[1], vectors[1]))
            self.assertEqual([f["e"] for f in rows], [0, 1])
            self.assertEqual(len(cache_rows), 2)
            self.assertEqual(corrections["faces"], {f"{pid}:0": "S100", f"{pid}:1": "none"})

            # Matching consumes only the rewritten cached embeddings and keeps
            # the old confirmed/rejected decisions at their historical indices.
            np.save(data / "ref_emb.npy", np.zeros((0, 512), dtype=np.float32))
            (data / "ref_ids.json").write_text("[]", encoding="utf-8")
            old_data = faces.DATA
            try:
                faces.DATA = data
                faces.match()
            finally:
                faces.DATA = old_data
            matched = json.loads((data / "faces.json").read_text(encoding="utf-8"))[pid]
            self.assertEqual([(f["i"], f["person"], f["status"]) for f in matched],
                             [(0, "S100", "confirmed"), (1, None, "rejected")])

            # A future extraction consumes the repaired cache exactly, with no
            # model loader and the same archived indices and boxes.
            source = data / "source"
            (source / "classroom").mkdir(parents=True)
            write_image(source / "classroom" / f"{pid}.webp", (10, 20, 30))
            old_state = (faces.DATA, faces.SRC, faces.GALLERY, faces.MODEL_TAG)
            try:
                faces.DATA, faces.SRC = data, source
                faces.GALLERY, faces.MODEL_TAG = data.parent / "gallery", "r50f"
                with patch.object(faces, "load_people", return_value={}), \
                     patch.object(faces, "_references", return_value=(0, 0, 0)), \
                     patch.object(faces, "_people_outputs"), \
                     patch.object(faces, "get_app", side_effect=AssertionError("model must not load")):
                    faces.extract()
            finally:
                faces.DATA, faces.SRC, faces.GALLERY, faces.MODEL_TAG = old_state
            replayed = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))[0]["faces"]
            self.assertEqual([f["bbox"] for f in replayed], [f["bbox"] for f in old_faces])
            self.assertEqual([f["i"] if "i" in f else index for index, f in enumerate(replayed)], [0, 1])
            self.assertFalse(any(f.get("embedding_missing") for f in replayed))

    def test_repair_never_reuses_one_candidate_for_two_ambiguous_faces(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            pid = "p00000003"
            old_faces = [
                {"i": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "person": "S100", "status": "confirmed"},
                {"i": 1, "bbox": [0.12, 0.1, 0.2, 0.2], "person": "S200", "status": "high"},
            ]
            photo = {"id": pid, "w": 1000, "h": 500, "faces": old_faces}
            (data / "catalog.json").write_text(json.dumps([{"id": pid, "width": 1000, "height": 500}]), encoding="utf-8")
            cache = data / "fx" / "r50f"
            cache.mkdir(parents=True)
            (cache / f"{pid}.json").write_text("[]", encoding="utf-8")
            vectors = np.zeros((3, 512), dtype=np.float32)
            vectors[2, 0] = 1.0
            raw = [{"photo": pid, "faces": [
                {"bbox": old_faces[0]["bbox"], "det": 0.0, "px": 100, "q": 0.0, "e": 0, "embedding_missing": True},
                {"bbox": old_faces[1]["bbox"], "det": 0.0, "px": 100, "q": 0.0, "e": 1, "embedding_missing": True},
                {"bbox": [0.11, 0.1, 0.2, 0.2], "det": 0.96, "px": 100, "q": 0.7, "e": 2},
            ]}]
            (data / "faces_raw.json").write_text(json.dumps(raw), encoding="utf-8")
            np.save(data / "face_emb.npy", vectors)

            counts = recover_archive.repair_cached_embeddings(data, [photo], model_tag="r50f")
            rows = json.loads((data / "faces_raw.json").read_text(encoding="utf-8"))[0]["faces"]
            self.assertEqual(counts["repaired_embeddings"], 0)
            self.assertEqual(counts["remaining_missing_embeddings"], 2)
            self.assertEqual(counts["removed_extra_detections"], 0)
            self.assertEqual(len(rows), 3)
            self.assertTrue(rows[0]["embedding_missing"])
            self.assertTrue(rows[1]["embedding_missing"])

    def test_ambiguous_or_shifted_boxes_are_reported_unresolved(self):
        deployed = [{"id": "p1", "faces": [
            {"i": 0, "bbox": [0.1, 0.1, 0.2, 0.2], "person": "S123", "status": "high"},
        ]}]
        raw = [{"photo": "p1", "faces": [
            {"bbox": [0.1, 0.1, 0.2, 0.2]}, {"bbox": [0.1, 0.1, 0.2, 0.2]},
        ]}]
        fixes, unresolved = recover_archive.transfer_confirmed_labels(deployed, raw)
        self.assertEqual(fixes, {})
        self.assertEqual(unresolved, [{"photo": "p1", "old_i": 0, "reason": "no_unique_bbox_match"}])


if __name__ == "__main__":
    unittest.main()
