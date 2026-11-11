import unittest

from auto_people import assign_ids, face_key, next_number


class AssignIdsTest(unittest.TestCase):
    def test_new_cluster_gets_next_id(self):
        mapping, reg = assign_ids({1: ["a", "b", "c"]}, {})
        self.assertEqual(mapping, {1: "U_1"})
        self.assertEqual(reg, {"U_1": ["a", "b", "c"]})

    def test_id_is_stable_when_label_renumbers(self):
        _, reg = assign_ids({1: ["a", "b", "c"], 2: ["d", "e"]}, {})
        # next run: fcluster swaps the labels and one face joins the first group
        mapping, reg2 = assign_ids({1: ["d", "e"], 2: ["a", "b", "c", "f"]}, reg)
        self.assertEqual(mapping, {1: "U_2", 2: "U_1"})
        self.assertEqual(sorted(reg2["U_1"]), ["a", "b", "c", "f"])

    def test_overlap_must_exceed_half_of_old_group(self):
        _, reg = assign_ids({1: ["a", "b", "c", "d"]}, {})
        mapping, _ = assign_ids({7: ["a", "x", "y"]}, reg)   # only 1 of 4 old members
        self.assertEqual(mapping, {7: "U_2"})

    def test_ids_never_reused_after_a_group_disappears(self):
        _, reg = assign_ids({1: ["a", "b"], 2: ["c", "d"]}, {})
        mapping, reg2 = assign_ids({1: ["a", "b"]}, reg)
        self.assertNotIn("U_2", reg2)
        mapping, _ = assign_ids({1: ["a", "b"], 2: ["z", "y"]}, reg2, floor=3)   # floor = persisted counter
        self.assertEqual(mapping[2], "U_3")

    def test_split_keeps_id_on_the_larger_half(self):
        _, reg = assign_ids({1: ["a", "b", "c", "d"]}, {})
        mapping, _ = assign_ids({1: ["a", "b", "c"], 2: ["d", "e"]}, reg)
        self.assertEqual(mapping[1], "U_1")
        self.assertEqual(mapping[2], "U_2")

    def test_two_clusters_never_share_an_id(self):
        _, reg = assign_ids({1: ["a", "b", "c", "d"]}, {})
        mapping, _ = assign_ids({1: ["a", "b", "c"], 2: ["a", "d", "e"]}, reg)
        self.assertEqual(len(set(mapping.values())), 2)

    def test_deterministic(self):
        c = {1: ["a", "b"], 2: ["c", "d"], 3: ["e", "f"]}
        self.assertEqual(assign_ids(c, {}), assign_ids(c, {}))


class HelpersTest(unittest.TestCase):
    def test_face_key_ignores_array_index(self):
        a = face_key("p1", [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(a, face_key("p1", [0.1000001, 0.2, 0.3, 0.4]))
        self.assertNotEqual(a, face_key("p2", [0.1, 0.2, 0.3, 0.4]))
        self.assertNotEqual(a, face_key("p1", [0.5, 0.2, 0.3, 0.4]))

    def test_next_number(self):
        self.assertEqual(next_number({}), 1)
        self.assertEqual(next_number({"U_3": [], "U_10": []}), 11)
        self.assertEqual(next_number({}, floor=7), 7)



class MatchIntegrationTest(unittest.TestCase):
    def test_unknown_faces_get_stable_unnamed_ids_across_runs(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch
        import numpy as np
        import faces

        def unit(*v):
            v = np.array(v, dtype=np.float32); return v / np.linalg.norm(v)
        # two tight groups of unknown faces (orthogonal directions) and one loner
        embs = [unit(1, 0, 0, 0.01 * i) for i in range(3)] + [unit(0, 1, 0, 0.01 * i) for i in range(2)] + [unit(0, 0, 1, 0)]
        rows = [{"photo": f"p{i}", "faces": [{"bbox": [.1, .1, .2, .2], "det": .9, "px": 80, "e": i}]} for i in range(len(embs))]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "faces_raw.json").write_text(json.dumps(rows))
            np.save(root / "face_emb.npy", np.array(embs))
            np.save(root / "ref_emb.npy", np.zeros((0, 4), dtype=np.float32))
            (root / "ref_ids.json").write_text("[]")
            def run():
                with patch.object(faces, "DATA", root):
                    faces.match()
                out = json.loads((root / "faces.json").read_text())
                return {k: v[0]["cluster"] for k, v in out.items()}
            first = run()
            self.assertEqual(first["p0"], first["p1"]); self.assertEqual(first["p3"], first["p4"])
            self.assertNotEqual(first["p0"], first["p3"])
            self.assertIsInstance(first["p0"], int)
            self.assertIsNone(first["p5"])                   # a single face is not a person yet
            self.assertEqual(run(), first)                   # re-running keeps the same ids


if __name__ == "__main__":
    unittest.main()
