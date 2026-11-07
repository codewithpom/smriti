import json
import tempfile
import unittest
from pathlib import Path

import obfuscate


class ObfuscateTest(unittest.TestCase):
    def test_renames_images_and_rewrites_every_reference(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "src"), Path(d, "out")
            (src / "data").mkdir(parents=True)
            for sub in ("web", "thumb", "people"):
                (src / "img" / sub).mkdir(parents=True)
            (src / "img/web/p1.webp").write_bytes(b"web")
            (src / "img/thumb/p1.webp").write_bytes(b"thumb")
            (src / "img/people/S1.webp").write_bytes(b"av")
            (src / "data/photos.json").write_text(json.dumps([{"id": "p1", "web": "img/web/p1.webp", "thumb": "img/thumb/p1.webp", "people": ["S1"]}]))
            (src / "data/people.json").write_text(json.dumps([{"id": "S1", "avatar": "img/people/S1.webp", "gone": "img/people/none.webp"}]))
            n, missing = obfuscate.build(src, out, b"k" * 32)
            self.assertEqual((n, missing), (3, 1))
            photos = json.loads((out / "data/photos.json").read_text())[0]
            avatar = json.loads((out / "data/people.json").read_text())[0]["avatar"]
            for ref in (photos["web"], photos["thumb"], avatar):
                self.assertTrue((out / ref).is_file())
                self.assertNotIn("p1", ref)
                self.assertNotIn("S1", ref)
            self.assertNotEqual(photos["web"], photos["thumb"])
            self.assertEqual(photos["id"], "p1")  # ids stay; only URLs are hidden
            names = sorted(p.name for p in (out / "img").iterdir())
            self.assertEqual(len(names), 3)
            # same key, same path -> same name (stable across deploys)
            again = Path(d, "out2")
            obfuscate.build(src, again, b"k" * 32)
            self.assertEqual(photos["web"], json.loads((again / "data/photos.json").read_text())[0]["web"])
            # different key -> different name
            other = Path(d, "out3")
            obfuscate.build(src, other, b"z" * 32)
            self.assertNotEqual(photos["web"], json.loads((other / "data/photos.json").read_text())[0]["web"])


if __name__ == "__main__":
    unittest.main()
