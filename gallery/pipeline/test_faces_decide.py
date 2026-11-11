import unittest

import numpy as np

from faces import decide, rank_persons, prototype_bank


def vector(x):
    return np.array([x, np.sqrt(1 - x*x), 0, 0, 0, 0, 0, 0])


class DecideTest(unittest.TestCase):
    def test_missing_recovered_embeddings_never_become_reference_faces(self):
        raw = [{"photo": "p1", "faces": [{"e": 0, "embedding_missing": True, "px": 80}]}]
        vectors, ids = prototype_bank(raw, np.array([vector(0)]), np.array([vector(1)]), ["known"], {"p1:0": "missing"})
        self.assertEqual(ids, ["known"])
        np.testing.assert_array_equal(vectors, np.array([vector(1)]))

    def test_max_and_top3_hand_computed(self):
        bank = np.array([vector(x) for x in (1.0, 0.0, 0.0, 0.6)])
        ids = ["alpha", "alpha", "alpha", "beta"]
        query = vector(1.0)
        maximum = rank_persons(bank, ids, query)
        top3 = rank_persons(bank, ids, query, "top3")
        self.assertEqual(maximum, [("alpha", 1.0), ("beta", 0.6)])
        self.assertEqual(top3, [("beta", 0.6), ("alpha", 1/3)])
        self.assertEqual(decide(maximum, 40), ("alpha", 1.0, "high"))
        self.assertEqual(decide(top3, 40), ("beta", 0.6, "high"))
        self.assertEqual(decide(maximum, 27), (None, 0.0, "tiny"))

    def test_boundaries_and_single_person(self):
        self.assertEqual(decide([("alpha", 0.42)], 28), ("alpha", 0.42, "high"))
        self.assertEqual(decide([("alpha", 0.5), ("beta", 0.49)], 28), (None, 0.5, "candidate"))
        self.assertEqual(decide([("alpha", 0.28)], 28), (None, 0.28, "candidate"))
        self.assertEqual(decide([("alpha", 0.27)], 28), (None, 0.0, "unknown"))
        self.assertEqual(decide([], 28), (None, 0.0, "unknown"))
        self.assertEqual(rank_persons([], [], vector(1)), [])


if __name__ == "__main__":
    unittest.main()
