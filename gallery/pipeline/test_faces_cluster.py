import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

import eval_match
import faces


METHODS = ("hdbscan", "average")


class ClusterTest(unittest.TestCase):
    def test_three_identities_and_noise(self):
        rng = np.random.default_rng(42)
        # Orthogonal identity directions and remote singleton noise on a unit sphere.
        e = np.vstack([np.eye(12)[i] + rng.normal(0, .01, (8, 12)) for i in range(3)]
                      + [np.eye(12)[3:6]])
        e = faces.unit(e)
        truth = [i // 8 for i in range(24)] + [-1] * 3
        for method in METHODS:
            with self.subTest(method=method):
                labels = faces.cluster_pool(e, [.9] * len(e), list(range(len(e))), method)
                self.assertEqual(eval_match.cluster_scores(labels, truth)["pairwise_f1"], 1)
                self.assertEqual(labels[-3:], [-1] * 3)

    def test_low_quality_only_attaches(self):
        e = faces.unit(np.array([[1, 0, 0, 0], [1, .01, 0, 0], [1, .02, 0, 0],
                                 [0, 1, 0, 0], [0, 1, .01, 0]], dtype=float))
        for method in METHODS:
            with self.subTest(method=method):
                labels = faces.cluster_pool(e, [.9, .9, .1, .1, .1], list(range(5)), method)
                self.assertGreaterEqual(labels[0], 0)
                self.assertEqual(labels[:3], [labels[0]] * 3)
                self.assertEqual(labels[3:], [-1, -1])
                self.assertEqual(faces.cluster_pool(e, [.1] * 5, list(range(5)), method), [-1] * 5)

    def test_cannot_link_keeps_nearest_and_drops_small_groups(self):
        e = faces.unit(np.array([[1, 0, 0], [1, .2, 0], [1, -.02, 0], [1, .01, 0]], dtype=float))
        for method in METHODS:
            with self.subTest(method=method):
                labels = faces.cluster_pool(e, [.9] * 4, [0, 0, 1, 2], method)
                self.assertEqual(labels[1], -1)
                self.assertGreaterEqual(labels[0], 0)
                self.assertEqual(labels[0], labels[2])
                self.assertEqual(faces.cluster_pool(e[:2], [.9] * 2, [0, 0], method), [-1, -1])

    def test_empty_singleton_and_invalid_method(self):
        for method in METHODS:
            self.assertEqual(faces.cluster_pool([], [], [], method), [])
            self.assertEqual(faces.cluster_pool([[1, 0]], [.9], [0], method), [-1])
            self.assertEqual(faces.cluster_pool(np.eye(2), [.9, .9], [0, 1], method), [-1, -1])
        with self.assertRaises(ValueError):
            faces.cluster_pool([], [], [], "invalid")

    def test_prototype_bank_and_thresholds(self):
        emb = faces.unit(np.array([[1, .2, 0], [1, -.1, 0], [0, 1, 0], [0, 0, 1]], dtype=float))
        raw = [{"photo": f"synthetic-{i}", "faces": [{"e": i, "px": 80, "q": q}]}
               for i, q in enumerate((.8, .4, .1, .9))]
        fixes = {f"synthetic-{i}:0": pid for i, pid in enumerate(("alpha", "alpha", "alpha", "beta"))}
        bank, ids = faces.prototype_bank(raw, emb, [emb[3]], ["beta"], fixes)
        self.assertEqual(len(bank), 6)  # portrait + all four confirmations + one core prototype
        self.assertEqual(list(ids), ["beta", "alpha", "alpha", "alpha", "beta", "alpha"])
        np.testing.assert_allclose(bank[-1], faces.unit((emb[0] * .8 + emb[1] * .4) / 1.2))
        # The prototype only changes the bank; quality can only RAISE the high cutoff.
        self.assertEqual(faces.decide([("alpha", faces.T_HIGH - .01)], 80)[2], "candidate")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            query = np.array([.46, np.sqrt(1 - .46 ** 2), 0])
            rows = [{"photo": f"synthetic-{i}", "faces": [
                {"e": i, "px": 80, "q": q, "bbox": [.1, .1, .2, .2]}]}
                for i, q in enumerate((.8, .1))]
            for name, value in (("faces_raw.json", rows), ("ref_ids.json", ["alpha"])):
                (root / name).write_text(json.dumps(value))
            np.save(root / "face_emb.npy", [query, query])
            np.save(root / "ref_emb.npy", [[1, 0, 0]])
            with patch.object(faces, "DATA", root), contextlib.redirect_stdout(io.StringIO()):
                faces.match()
            result = json.loads((root / "faces.json").read_text())
            self.assertEqual(result["synthetic-0"][0]["status"], "high")
            self.assertEqual(result["synthetic-1"][0]["status"], "candidate")


class ScoresTest(unittest.TestCase):
    def test_known_partitions(self):
        truth = [0, 0, 1, 1]
        perfect = eval_match.cluster_scores([4, 4, 8, 8], truth)
        self.assertTrue(all(v == 1 for v in perfect.values()))
        merged = eval_match.cluster_scores([0] * 4, truth)
        self.assertAlmostEqual(merged["pairwise_p"], 1 / 3)
        self.assertEqual(merged["pairwise_r"], 1)
        self.assertAlmostEqual(merged["pairwise_f1"], .5)
        self.assertEqual(merged["bcubed_p"], .5)
        self.assertEqual(merged["bcubed_r"], 1)
        self.assertAlmostEqual(merged["bcubed_f1"], 2 / 3)
        single = eval_match.cluster_scores([-1] * 4, truth)
        self.assertEqual(single["pairwise_r"], 0)
        self.assertEqual(single["pairwise_f1"], 0)
        self.assertEqual(single["bcubed_p"], 1)
        self.assertEqual(single["bcubed_r"], .5)
        self.assertAlmostEqual(single["bcubed_f1"], 2 / 3)
        self.assertTrue(all(v == 1 for v in eval_match.cluster_scores([-1, None], [0, 1]).values()))
        self.assertTrue(all(v == 0 for v in eval_match.cluster_scores([], []).values()))
        with self.assertRaises(ValueError):
            eval_match.cluster_scores([0], [])

    def test_cli_is_read_only_and_anonymous(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rows = [{"photo": f"synthetic-{i}", "faces": [{"e": i, "px": 80, "q": .9}]}
                    for i in range(4)]
            for name, value in (("faces_raw.json", rows), ("ref_ids.json", []),
                                ("corrections.json", {"faces": {f"synthetic-{i}:0": f"identity-{i // 2}" for i in range(4)}})):
                (root / name).write_text(json.dumps(value))
            np.save(root / "face_emb.npy", [[1, 0, 0], [1, .01, 0], [0, 1, 0], [0, 1, .01]])
            np.save(root / "ref_emb.npy", np.empty((0, 3)))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                eval_match.main(["--data", d, "--clusters", "--bank", "refs", "--agg", "max"])
            for method in METHODS:
                self.assertIn(method, output.getvalue())
            for private in ("synthetic-", "identity-"):
                self.assertNotIn(private, output.getvalue())
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


class EmbeddingAndCacheTest(unittest.TestCase):
    def test_new_extract_writes_v2_and_counts_oom(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); data = root / "data"; data.mkdir()
            Image.new("RGB", (100, 100), "white").save(root / "synthetic.jpg")
            (data / "catalog.json").write_text(json.dumps([{"id": "synthetic", "file": "synthetic.jpg"}]))
            kps = np.array([[30, 40], [70, 40], [50, 60], [35, 75], [65, 75]], dtype=float)
            face = types.SimpleNamespace(bbox=np.array([10, 10, 90, 90]), kps=kps, det_score=.9)
            app = unittest.mock.Mock(); app.get.return_value = [face]
            app.models = {"recognition": unittest.mock.Mock()}
            app.models["recognition"].get_feat.return_value = np.repeat(np.eye(1, 512), 2, axis=0)
            with patch.multiple(faces, DATA=data, GALLERY=root, SRC=root, MODEL_TAG="r50f"), \
                    patch.object(faces, "load_people", return_value={}), \
                    patch.object(faces, "get_app", return_value=app), \
                    patch.object(faces, "aligned_face", return_value=np.ones((112, 112, 3), dtype=np.uint8)):
                with contextlib.redirect_stdout(io.StringIO()):
                    faces.extract()
                cache = data / "fx" / "r50f" / "synthetic.json"
                row = json.loads(cache.read_text())[0]
                self.assertEqual(set(row), {"b", "k", "w", "h", "d", "q", "v"})
                np.testing.assert_array_equal(row["k"], kps)
                self.assertAlmostEqual(np.linalg.norm(row["v"]), 1)
                # A different uncached photo hits the bounded retry path.
                (data / "catalog.json").write_text(json.dumps([{"id": "synthetic-oom", "file": "synthetic.jpg"}]))
                app.get.side_effect = MemoryError
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    faces.extract()
                self.assertIn("OOM 1", output.getvalue())
                self.assertNotIn("synthetic", output.getvalue())
                self.assertEqual(np.load(data / "face_emb.npy").shape, (0, 512))

    def test_flip_normalisation_and_ada_preprocessing(self):
        aligned = np.arange(112 * 112 * 3, dtype=np.uint8).reshape(112, 112, 3)
        rec = unittest.mock.Mock()
        rec.get_feat.return_value = np.array([[3., 0], [0, 4.]])
        app = types.SimpleNamespace(models={"recognition": rec})
        ada = unittest.mock.Mock()
        ada.get_inputs.return_value = [types.SimpleNamespace(name="images")]
        ada.run.return_value = [np.array([[0., 2], [0, 4.]])]
        fused = faces.embed_aligned(app, aligned, ada)
        np.testing.assert_allclose(fused, [.5, .5, 0, 1 / np.sqrt(2)])
        crops = rec.get_feat.call_args.args[0]
        np.testing.assert_array_equal(crops[1], aligned[:, ::-1])
        batch = ada.run.call_args.args[1]["images"]
        self.assertEqual(batch.shape, (2, 3, 112, 112))
        np.testing.assert_allclose(batch[0], ((aligned[:, :, ::-1].astype(np.float32) / 255 - .5) / .5).transpose(2, 0, 1))

    def test_cached_extract_without_sources_or_models(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); data = root / "data"; data.mkdir()
            cache = data / "fx"; (cache / "r50f").mkdir(parents=True)
            row = {"b": [10, 10, 90, 90], "w": 100, "h": 100, "d": .9, "v": np.eye(1, 512)[0].tolist()}
            (cache / "synthetic-0.json").write_text(json.dumps([row]))
            (cache / "r50f" / "synthetic-1.json").write_text(json.dumps([{**row, "q": .8, "k": [[0, 0]] * 5}]))
            (data / "catalog.json").write_text(json.dumps([
                {"id": f"synthetic-{i}", "file": f"missing-{i}.jpg"} for i in range(3)]))
            with patch.multiple(faces, DATA=data, GALLERY=root, SRC=root, MODEL_TAG="r50f"), \
                    patch.object(faces, "load_people", return_value={}), \
                    patch.object(faces, "get_app", side_effect=AssertionError("cached run loaded model")):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    faces.extract()
                self.assertNotIn("synthetic-", output.getvalue())
                self.assertIn("skipped missing 1", output.getvalue())
                raw = json.loads((data / "faces_raw.json").read_text())
                self.assertEqual(len(raw), 2)
                self.assertAlmostEqual(raw[0]["faces"][0]["q"], faces.quality_without_crop(.9, 80))
                self.assertEqual(raw[1]["faces"][0]["q"], .8)
                self.assertEqual(np.load(data / "face_emb.npy").shape, (2, 512))
                with contextlib.redirect_stdout(output):
                    faces.extract(reembed=True)
                # no source on this machine: reembed keeps the cached faces rather than dropping the photo
                self.assertEqual(len(json.loads((data / "faces_raw.json").read_text())), 2)

    def test_incremental_portraits_and_avatars(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); data = root / "data"; data.mkdir()
            portrait = root / "synthetic.jpg"
            Image.new("RGB", (32, 32), "white").save(portrait)
            people = {"synthetic": {"portrait_src": portrait}}
            face = types.SimpleNamespace(bbox=np.array([0, 0, 32, 32]))
            app = unittest.mock.Mock(); app.get.return_value = [face]
            load = unittest.mock.Mock(return_value=app)
            with patch.multiple(faces, DATA=data, GALLERY=root, MODEL_TAG="r50f"), \
                    patch.object(faces, "aligned_face", return_value=np.zeros((112, 112, 3))), \
                    patch.object(faces, "embed_aligned", return_value=np.eye(1, 512)[0]):
                self.assertEqual(faces._references(people, load, None), (1, 0, 1))
                faces._people_outputs(people)
                avatar = root / "img" / "people" / "synthetic.webp"
                avatar_before = avatar.read_bytes()
                load.reset_mock()
                self.assertEqual(faces._references(people, load, None), (1, 0, 0))
                load.assert_not_called()
                Image.new("RGB", (32, 32), "black").save(portrait)
                self.assertEqual(faces._references(people, load, None), (1, 0, 1))
                faces._people_outputs(people)
                self.assertEqual(avatar.read_bytes(), avatar_before)
                # No portrait on this runner: retain the cached reference.
                people["synthetic"]["portrait_src"] = root / "absent.jpg"
                self.assertEqual(faces._references(people, load, None), (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
