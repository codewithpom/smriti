import unittest

import cv2
import numpy as np

from face_quality import quality, quality_without_crop


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.kps = np.array([[32, 40], [80, 40], [56, 62], [38, 84], [74, 84]], dtype=float)
        rng = np.random.default_rng(7)
        self.sharp = rng.integers(0, 256, (112, 112, 3), dtype=np.uint8)

    def test_components_monotonic(self):
        scores = [quality(det, 80, self.kps, self.sharp) for det in (.5, .6, .7, .8, .9)]
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[1])
        scores = [quality(.9, px, self.kps, self.sharp) for px in (24, 28, 40, 80, 96)]
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[1])
        scores = [quality(.9, 80, self.kps, cv2.GaussianBlur(self.sharp, (0, 0), sigma))
                  for sigma in (8, 4, 2, 1)]
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[0])
        scores = []
        for dx in (0, 4, 8, 12, 16):
            kps = self.kps.copy(); kps[2, 0] += dx
            scores.append(quality(.9, 80, kps, self.sharp))
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(scores[0], scores[-1])

    def test_frontal_sharp_big_beats_profile_blurry_small(self):
        profile = self.kps.copy(); profile[2, 0] += 12
        self.assertGreater(quality(.9, 96, self.kps, self.sharp),
                           quality(.65, 30, profile, cv2.GaussianBlur(self.sharp, (0, 0), 5)))

    def test_range_and_degenerate_inputs(self):
        for det in (-1, .5, .9, 2):
            for px in (0, 24, 96, 500):
                for kps in (self.kps, np.zeros((5, 2))):
                    score = quality(det, px, kps, self.sharp)
                    self.assertTrue(0 <= score <= 1)
        self.assertEqual(quality(.9, 96, self.kps, np.zeros((112, 112, 3), dtype=np.uint8)), 0)
        self.assertEqual(quality(.9, 96, self.kps, np.empty((0, 0, 3), dtype=np.uint8)), 0)

    def test_legacy(self):
        self.assertAlmostEqual(quality_without_crop(.9, 96), .7 ** .45)
        self.assertEqual(quality_without_crop(.9, 24), 0)
        self.assertGreater(quality_without_crop(.9, 80), quality_without_crop(.7, 40))
        for det in (-1, .6, 1):
            for px in (-1, 40, 200):
                self.assertTrue(0 <= quality_without_crop(det, px) <= 1)


if __name__ == "__main__":
    unittest.main()
