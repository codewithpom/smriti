import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import dedupe


class DedupeTest(unittest.TestCase):
    def test_vectors_match(self):
        vectors = json.loads((Path(__file__).parent / "testdata" / "phash_vectors.json").read_text())
        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(dedupe.phash_gray(np.array(vector["gray"], dtype=np.float64)), vector["hash"])

    def test_jpeg_reencode_and_resize_is_duplicate(self):
        y, x = np.mgrid[:120, :160]
        rgb = np.stack(((x * 3 + y) % 256, (x + y * 2) % 256, (x * 2 + y * 3) % 256), axis=2).astype("uint8")
        original = Image.fromarray(rgb)
        changed = original.resize((112, 84), Image.Resampling.BILINEAR)
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.jpg", Path(directory) / "b.jpg"
            original.save(a, quality=95)
            changed.save(b, quality=60)
            self.assertEqual(dedupe.classify(dedupe.image_info(a), dedupe.image_info(b)), "duplicate")

    def test_unrelated_synthetic_images_are_not_similar(self):
        first = np.fromfunction(lambda y, x: ((x // 3 + y // 5) % 2) * 255, (80, 80))
        second = np.fromfunction(lambda y, x: ((x + y * 3) % 17) * 15, (80, 80))
        a = {"sha": "a", "ph": dedupe.phash_gray(first), "w": 80, "h": 80}
        b = {"sha": "b", "ph": dedupe.phash_gray(second), "w": 80, "h": 80}
        self.assertIsNone(dedupe.classify(a, b))

    def test_aspect_and_sha_rules(self):
        a = {"sha": "a", "ph": "0000000000000000", "w": 160, "h": 100}
        b = {"sha": "b", "ph": "0000000000000000", "w": 100, "h": 100}
        self.assertEqual(dedupe.classify(a, b), "similar")
        b["sha"] = "a"
        self.assertEqual(dedupe.classify(a, b), "duplicate")

    def test_group_keeps_highest_score_and_ties_in_input_order(self):
        entries = [
            {"id": "first", "sha": "x", "ph": "0" * 16, "w": 10, "h": 10},
            {"id": "best", "sha": "x", "ph": "f" * 16, "w": 10, "h": 10},
            {"id": "other", "sha": "z", "ph": "0" * 16, "w": 10, "h": 10},
        ]
        self.assertEqual(dedupe.group_duplicates(entries, lambda e: {"first": 4, "best": 5, "other": 5}[e["id"]]),
                         {"first": "best", "other": "best"})

    def test_find_match_prefers_duplicate_then_smallest_distance_then_order(self):
        item = {"sha": "item", "ph": "0" * 16, "w": 10, "h": 10}
        index = [
            {"id": "similar", "sha": "s", "ph": "0000000000000fff", "w": 10, "h": 10},
            {"id": "duplicate-first", "sha": "d1", "ph": "000000000000003f", "w": 10, "h": 10},
            {"id": "duplicate-best", "sha": "d2", "ph": "0000000000000003", "w": 10, "h": 10},
            {"id": "duplicate-tie", "sha": "d3", "ph": "0000000000000003", "w": 10, "h": 10},
        ]
        self.assertEqual(dedupe.find_match(item, index), ("duplicate", "duplicate-best"))


if __name__ == "__main__":
    unittest.main()
