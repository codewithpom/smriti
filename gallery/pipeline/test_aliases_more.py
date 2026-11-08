import unittest

import faces


def staff(i, name, role=""):
    return {"id": f"STAFF_{i:03d}", "name": name, "role": role}


class MoreAliases(unittest.TestCase):
    def test_sri_and_shri_are_the_same_honorific(self):
        a = faces.staff_aliases([staff(28, "Sri Test One", "Maths"), staff(128, "Shri Test One"), staff(139, "Sri Test One")])
        self.assertEqual(a, {"STAFF_128": "STAFF_028", "STAFF_139": "STAFF_028"})

    def test_different_people_stay_apart(self):
        self.assertEqual(faces.staff_aliases([staff(1, "Sri Test One"), staff(2, "Sri Test Two")]), {})

    def test_manual_merge_and_chains(self):
        auto = {"STAFF_141": "STAFF_082"}
        merged = faces.merge_aliases(auto, {"STAFF_195": "STAFF_141"})
        self.assertEqual(merged, {"STAFF_141": "STAFF_082", "STAFF_195": "STAFF_082"})

    def test_cycle_and_self_alias_are_dropped_safely(self):
        merged = faces.merge_aliases({}, {"A": "B", "B": "A", "C": "C"})
        self.assertNotIn("C", merged)
        self.assertTrue(all(d != c for d, c in merged.items()))   # terminates, no id maps to itself


if __name__ == "__main__":
    unittest.main()
