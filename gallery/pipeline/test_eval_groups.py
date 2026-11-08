import unittest

import numpy as np

import eval_match


def setup(extra_single):
    e = np.eye(4, dtype=np.float32)            # row 0: person A's look, row 1: person B's look
    face = lambda row: {"bbox": [0.1, 0.1, 0.2, 0.2], "det": 0.9, "px": 60, "e": row}
    raw = [{"photo": "p1", "faces": [face(0), face(0)]}]
    faces = {"p1:0": "A", "p1:1": "A"}
    if extra_single:
        raw.append({"photo": "p2", "faces": [face(0)]})
        faces["p2:0"] = "A"
    corrections = {"faces": faces, "history": [{"keys": ["p1:0", "p1:1"]}]}
    return raw, e, np.array([e[1]]), ["B"], corrections


class LeaveGroupOut(unittest.TestCase):
    def test_cluster_mates_are_held_out_together(self):
        # A has no portrait and its only labels are one cluster answer: nothing independent to test against.
        trials, stats = eval_match.leave_one_out_trials(*setup(extra_single=False))
        self.assertEqual((stats["total"], stats["unreachable"], len(trials)), (2, 2, 0))

    def test_independent_label_makes_trials_reachable(self):
        trials, stats = eval_match.leave_one_out_trials(*setup(extra_single=True))
        self.assertEqual((stats["total"], stats["unreachable"]), (3, 0))
        # the cluster's faces are scored against the independent p2:0 label; p2:0 against the cluster
        self.assertTrue(all(ranked[0][0] == "A" for _, _, ranked in trials))


if __name__ == "__main__":
    unittest.main()
