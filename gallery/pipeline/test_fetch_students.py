import io, json, tempfile, unittest
from pathlib import Path

from PIL import Image

import fetch_students as fs


def jpg(color):
    b = io.BytesIO(); Image.new("RGB", (80, 80), color).save(b, "JPEG"); return b.getvalue() + b"0" * 6000


class FetchStudents(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "extra_students.json").write_text(json.dumps([{"reg_no": "10001", "name": "Old", "section": "A", "roll_sl": 1}]))
        (self.d / "people_meta.json").write_text(json.dumps({"S10002": {"reg_no": "10002"}}))

    def fake(self, reg, delay):
        if reg == 10003: return reg, {"sec": "B", "sl_no": 7, "total": 500.0, "percent": 80.0, "name": "X"}, jpg("red")
        if reg == 10004: return reg, None, jpg("blue")       # image only
        if reg in (10005, 10006, 10007): return reg, None, b"same" * 2000  # placeholder x3
        return reg, None, None

    def test_run(self):
        s = fs.run(10001, 10008, "MB28", self.d, delay=0, fetch=lambda r, dl: self.fake(r, dl))
        self.assertEqual(s["already"], 2)
        out = json.loads((self.d / "extra_students.json").read_text())
        self.assertEqual([e["reg_no"] for e in out], ["10001", "10003", "10004"])
        new = {e["reg_no"]: e for e in out}
        self.assertEqual(new["10003"]["batch"], "MB28"); self.assertEqual(new["10003"]["name"], "")
        self.assertEqual(new["10003"]["section"], "B"); self.assertEqual(new["10003"]["roll_sl"], 7)
        self.assertTrue((self.d / "extra_refs" / "10003.jpg").exists())
        self.assertFalse((self.d / "extra_refs" / "10005.jpg").exists())
        self.assertEqual(s["placeholder"], 3)

    def test_never_stores_marks(self):
        fs.run(10003, 10003, "MB28", self.d, delay=0, fetch=lambda r, dl: self.fake(r, dl))
        text = (self.d / "extra_students.json").read_text()
        self.assertNotIn("percent", text); self.assertNotIn("total", text)

    def test_dry_run_writes_nothing(self):
        before = (self.d / "extra_students.json").read_text()
        fs.run(10003, 10004, "MB28", self.d, delay=0, dry=True, fetch=lambda r, dl: self.fake(r, dl))
        self.assertEqual(before, (self.d / "extra_students.json").read_text())
        self.assertFalse((self.d / "extra_refs").exists())


if __name__ == "__main__":
    unittest.main()
