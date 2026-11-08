import contextlib
import io
import json
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import faces
from test_faces_decide import vector


class AliasesTest(unittest.TestCase):
    def test_normalisation_and_role_preference(self):
        staff = [
            {"id": "STAFF_002", "name": "Example Person", "role": ""},
            {"id": "STAFF_120", "name": "EXAMPLE-PERSON (acting)", "role": "head"},
            {"id": "STAFF_135", "name": "Example. Person", "role": None},
        ]
        self.assertEqual(faces.staff_aliases(staff), {"STAFF_002": "STAFF_120", "STAFF_135": "STAFF_120"})
        staff[1]["role"] = None
        self.assertEqual(faces.staff_aliases(staff), {"STAFF_120": "STAFF_002", "STAFF_135": "STAFF_002"})

    def test_load_people_preserves_students(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = {"students": [{"reg_no": reg, "name": "Student placeholder"} for reg in ("001", "002")],
                   "staff": [{"id": pid, "name": "Example Person", "role": role}
                             for pid, role in (("STAFF_001", None), ("STAFF_002", "head"))]}
            (root / "identities.json").write_text(json.dumps(raw))
            with patch.object(faces, "HELLO", root), patch.object(faces, "DATA", root):
                people = faces.load_people()
                self.assertEqual(set(people), {"S001", "S002", "STAFF_002"})
                self.assertEqual(faces.load_aliases(), {"STAFF_001": "STAFF_002"})

    def test_match_maps_references_and_corrections(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = [{"photo": photo, "faces": [{"e": i, "px": 40, "bbox": [0, 0, 40, 40]}]}
                   for i, photo in enumerate(("auto", "fixed", "rejected"))]
            files = {"faces_raw.json": raw, "ref_ids.json": ["STAFF_001", "STAFF_002"],
                     "aliases.json": {"STAFF_002": "STAFF_001"},
                     "corrections.json": {"faces": {"fixed:0": "STAFF_002", "rejected:0": "none"}}}
            for name, value in files.items():
                (root / name).write_text(json.dumps(value))
            np.save(root / "face_emb.npy", np.array([vector(1)] * 3))
            np.save(root / "ref_emb.npy", np.array([vector(1), vector(0.99)]))
            hierarchy = types.ModuleType("scipy.cluster.hierarchy")
            hierarchy.fcluster = hierarchy.linkage = None  # no unknown faces to cluster
            with patch.object(faces, "DATA", root), patch.dict("sys.modules", {"scipy.cluster.hierarchy": hierarchy}):
                with contextlib.redirect_stdout(io.StringIO()):
                    faces.match()
            result = json.loads((root / "faces.json").read_text())
            self.assertEqual(result["auto"][0]["person"], "STAFF_001")
            self.assertEqual(result["auto"][0]["status"], "high")
            self.assertEqual(result["fixed"][0]["person"], "STAFF_001")
            self.assertEqual(result["fixed"][0]["status"], "confirmed")
            self.assertEqual(result["rejected"][0]["status"], "rejected")

    def test_alias_references_compete_as_one_person(self):
        bank = np.array([vector(x) for x in (1.0, 0.99, 0.3)])
        ids = ["STAFF_001", "STAFF_002", "S001"]
        aliases = {"STAFF_002": "STAFF_001"}
        self.assertEqual(faces.decide(faces.rank_persons(bank, ids, vector(1)), 40)[2], "candidate")
        mapped = [aliases.get(pid, pid) for pid in ids]
        self.assertEqual(faces.decide(faces.rank_persons(bank, mapped, vector(1)), 40), ("STAFF_001", 1.0, "high"))


if __name__ == "__main__":
    unittest.main()
