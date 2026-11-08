import json
import tempfile
import unittest
from pathlib import Path

import filter_batch


def face(status, person=None, i=0):
    return {"i": i, "bbox": [0, 0, 1, 1], "person": person, "score": 0.5, "status": status, "cands": [], "cluster": None}


CATALOG = [{"id": f"p{n}", "file": f"album/p{n}.jpg"} for n in range(1, 8)]
FACES = {
    "p1": [face("high", "S1"), face("unknown", None, 1)],          # one confident student -> keep
    "p2": [face("confirmed", "S2")],                                # admin-confirmed -> keep
    "p3": [face("candidate", "S3"), face("unknown", None, 1)],      # only maybes -> drop
    "p4": [face("tiny"), face("rejected")],                         # nothing usable -> drop
    "p5": [],                                                        # no faces -> drop
    "p6": [face("high", "S1"), face("high", "S1", 1)],              # same student twice
    # p7 never processed -> counts as no faces
}


class SelectTest(unittest.TestCase):
    def test_keeps_photos_with_a_confident_student_face(self):
        kept, counts = filter_batch.select(CATALOG, FACES)
        self.assertEqual(kept, ["p1", "p2", "p6"])
        self.assertEqual(counts, {"photos": 7, "kept": 3, "no_faces": 2, "no_match": 2})

    def test_min_students_counts_distinct_people(self):
        kept, counts = filter_batch.select(CATALOG, FACES, min_students=2)
        self.assertEqual(kept, [])  # p6 has two faces but one student
        self.assertEqual(counts["kept"], 0)
        faces = {**FACES, "p2": [face("confirmed", "S2"), face("high", "S9", 1)]}
        self.assertEqual(filter_batch.select(CATALOG, faces, min_students=2)[0], ["p2"])

    def test_staff_only_counts_without_student_filter(self):
        catalog = [{"id": "p1"}]
        faces = {"p1": [face("high", "STAFF_001")]}
        self.assertEqual(filter_batch.select(catalog, faces)[0], ["p1"])
        kept, counts = filter_batch.select(catalog, faces, student_ids={"S001"})
        self.assertEqual(kept, [])
        self.assertEqual(counts["no_match"], 1)

    def test_counts_add_up(self):
        _, c = filter_batch.select(CATALOG, FACES)
        self.assertEqual(c["kept"] + c["no_faces"] + c["no_match"], c["photos"])


class CopyTest(unittest.TestCase):
    def test_copies_only_kept_files_keeping_relative_paths_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "src"), Path(d, "out")
            (src / "album").mkdir(parents=True)
            for e in CATALOG:
                (src / e["file"]).write_bytes(e["id"].encode())
            n = filter_batch.copy_kept(CATALOG, ["p1", "p6"], src, out)
            self.assertEqual(n, 2)
            self.assertEqual(sorted(p.name for p in (out / "album").iterdir()), ["p1.jpg", "p6.jpg"])
            (out / "album/p1.jpg").write_bytes(b"edited")
            self.assertEqual(filter_batch.copy_kept(CATALOG, ["p1"], src, out), 0)  # existing file is left alone
            self.assertEqual((out / "album/p1.jpg").read_bytes(), b"edited")

    def test_missing_source_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(filter_batch.copy_kept(CATALOG, ["p1"], Path(d, "src"), Path(d, "out")), 0)


class CliTest(unittest.TestCase):
    def test_cli_people_filters_staff(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, value in (("catalog.json", [{"id": "p1", "file": "p1.jpg"}]),
                                ("faces.json", {"p1": [face("confirmed", "STAFF_001")]}),
                                ("people.json", {"STAFF_001": {"kind": "staff"}, "S001": {"kind": "student"}})):
                (root / name).write_text(json.dumps(value))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                filter_batch.main(["--catalog", str(root / "catalog.json"), "--faces", str(root / "faces.json"),
                                   "--people", str(root / "people.json"), "--src", str(root), "--out", str(root / "out")])
            self.assertIn("kept 0 of 1", output.getvalue())

    def test_cli_prints_counts_only_no_names_or_ids(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "src"), Path(d, "out")
            (src / "album").mkdir(parents=True)
            for e in CATALOG:
                (src / e["file"]).write_bytes(b"x")
            Path(d, "catalog.json").write_text(json.dumps(CATALOG))
            Path(d, "faces.json").write_text(json.dumps(FACES))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                filter_batch.main(["--catalog", str(Path(d, "catalog.json")), "--faces", str(Path(d, "faces.json")), "--src", str(src), "--out", str(out)])
            text = buf.getvalue()
            self.assertIn("kept 3 of 7", text)
            for secret in ("S1", "S2", "p1", "album"):
                self.assertNotIn(secret, text)
            self.assertEqual(len(list((out / "album").iterdir())), 3)


if __name__ == "__main__":
    unittest.main()
