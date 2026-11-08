import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import eval_match
from test_faces_decide import vector


def fixture(path, vectors, people, refs, ref_ids, sizes=None):
    raw = [{"photo": f"photo{n}", "faces": [
        {"bbox": [0, 0, 40, 40], "det": 1.0, "px": sizes[n] if sizes else 40, "e": n}
    ]} for n in range(len(vectors))]
    corrections = {"faces": {f"photo{n}:0": person for n, person in enumerate(people)}}
    for name, value in (("faces_raw.json", raw), ("ref_ids.json", ref_ids), ("corrections.json", corrections)):
        (path / name).write_text(json.dumps(value), encoding="utf-8")
    emb, ref_emb = np.array(vectors), np.array(refs).reshape(-1, 8)
    np.save(path / "face_emb.npy", emb)
    np.save(path / "ref_emb.npy", ref_emb)
    return raw, emb, ref_emb, ref_ids, corrections


class EvalTest(unittest.TestCase):
    def test_perfect_separation_and_read_only_cli(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            basis = np.eye(8)
            data = fixture(path, basis[:2], ["alpha", "beta"], basis[:2], ["alpha", "beta"])
            for bank in ("refs", "refs+confirmed"):
                for agg in ("max", "top3"):
                    trials, stats = eval_match.leave_one_out_trials(*data, bank, agg)
                    row = eval_match.sweep(trials, [0.5])[0]
                    self.assertEqual((row["precision"], row["recall"]), (1.0, 1.0))
                    self.assertEqual(stats["unreachable"], 0)
            before = {p.name: p.read_bytes() for p in path.iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                eval_match.main(["--data", d])
            self.assertIn("lowest T_HIGH=0.30", output.getvalue())
            for secret in ("alpha", "beta", "photo0"):
                self.assertNotIn(secret, output.getvalue())
            self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_confusable_pair_and_target(self):
        # alpha truth points 0.4 towards beta's portrait; beta truth matches perfectly.
        basis = np.eye(8)
        query = np.zeros(8)
        query[1], query[2] = 0.4, np.sqrt(0.84)
        with tempfile.TemporaryDirectory() as d:
            data = fixture(Path(d), [query, basis[1]], ["alpha", "beta"], basis[:2], ["alpha", "beta"])
            trials, _ = eval_match.leave_one_out_trials(*data, "refs")
            rows = eval_match.sweep(trials, [0.30, 0.50])
            self.assertEqual(rows[0]["precision"], 0.5)
            self.assertEqual(rows[1]["precision"], 1.0)
            self.assertEqual(rows[1]["recall"], 0.5)
            self.assertIn("lowest T_HIGH=0.50", eval_match.target_line("refs", "max", rows, 0.98))
            self.assertIn("none reaches target", eval_match.target_line("refs", "max", rows[:1], 0.98))

    def test_leave_one_out_unreachable_tiny_and_bias(self):
        basis = np.eye(8)
        with tempfile.TemporaryDirectory() as d:
            data = fixture(Path(d), [basis[0], basis[0], basis[1]],
                           ["alpha", "alpha", "beta"], [], [], [40, 20, 40])
            trials, stats = eval_match.leave_one_out_trials(*data)
            self.assertEqual(stats, {"total": 3, "unreachable": 1, "tiny": 1, "people": 2, "biased": True})
            row = eval_match.sweep(trials, [0.5])[0]
            self.assertEqual((row["accepted"], row["recall"]), (1, 0.5))
            self.assertEqual(eval_match.leave_one_out_trials(*data, "refs")[1]["unreachable"], 3)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                eval_match.main(["--data", d, "--bank", "refs+confirmed", "--agg", "max"])
            self.assertIn("more than 50%", output.getvalue())
            self.assertNotIn("alpha", output.getvalue())

    def test_empty_truth_and_none(self):
        with tempfile.TemporaryDirectory() as d:
            data = fixture(Path(d), [vector(1)], ["none"], [], [])
            trials, stats = eval_match.leave_one_out_trials(*data)
            self.assertEqual(stats["total"], 0)
            self.assertFalse(stats["biased"])
            self.assertEqual(eval_match.sweep(trials, [0.5])[0]["recall"], 0)


if __name__ == "__main__":
    unittest.main()
