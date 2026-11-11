import json
from pathlib import Path
import tempfile
import unittest

from recover_ci import verify_archive


class RecoveryInvariantTest(unittest.TestCase):
    def test_refuses_loss_of_historical_faces_and_metadata(self):
        old = [{"id": "p12345678", "w": 1600, "h": 900, "date": None,
                "faces": [{"i": 0, "bbox": [0.1, 0.2, 0.1, 0.1], "person": "S12345", "status": "high"}]}]
        people = [{"id": "S12345"}]
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            (data / "people.json").write_text(json.dumps(people))
            (data / "photos.json").write_text(json.dumps(old))
            self.assertEqual(verify_archive(old, people, data)["preserved_labels"], 1)
            for change in ("person", "bbox", "dimensions", "missing_face", "missing_photo"):
                with self.subTest(change=change):
                    changed = json.loads(json.dumps(old))
                    if change == "person": changed[0]["faces"][0]["person"] = "S54321"
                    elif change == "bbox": changed[0]["faces"][0]["bbox"][0] = 0.8
                    elif change == "dimensions": changed[0]["w"] = 800
                    elif change == "missing_face": changed[0]["faces"] = []
                    else: changed = []
                    (data / "photos.json").write_text(json.dumps(changed))
                    with self.assertRaises(RuntimeError): verify_archive(old, people, data)


if __name__ == "__main__":
    unittest.main()
