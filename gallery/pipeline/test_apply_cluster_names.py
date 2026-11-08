import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import apply_cluster_names
import name_sheet
from test_name_sheet import fixture, write


class ApplyTest(unittest.TestCase):
    def run_apply(self, data, answers, *flags):
        path = data.parent / "answers.json"
        write(path, answers)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = apply_cluster_names.main([str(path), "--data", str(data), *flags])
        self.assertNotIn("S0001", output.getvalue())
        self.assertNotIn("Test One", output.getvalue())
        return code, output.getvalue()

    def test_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            code, output = self.run_apply(data, {"fingerprint": name_sheet.fingerprint(groups), "answers": {"1": "S0001", "2": "none"}})
            self.assertEqual(code, 0)
            self.assertIn("applied 2", output)
            corr = name_sheet.load(data / "corrections.json")
            self.assertEqual(corr["faces"], {"p1:0": "S0001", "p1:1": "S0001", "p1:2": "none", "p1:3": "none"})
            self.assertEqual(len(corr["history"]), 2)
            self.assertEqual(name_sheet.load(data / "questions.json"), [])

    def test_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            before = (data / "corrections.json").read_bytes()
            code, output = self.run_apply(data, {"fingerprint": "bad", "answers": {"1": "S0001"}})
            self.assertEqual(code, 2)
            self.assertIn("data changed since the sheet was made, regenerate it", output)
            self.assertEqual(before, (data / "corrections.json").read_bytes())

    def test_dry_unknown_skip_missing_and_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            before = {p.name: p.read_bytes() for p in data.iterdir()}
            doc = {"fingerprint": name_sheet.fingerprint(groups), "answers": {"1": "S0001", "2": "invalid", "3": "skip", "4": "none"}}
            code, output = self.run_apply(data, doc, "--dry-run")
            self.assertEqual(code, 0)
            self.assertIn("applied 1, skipped 1, unknown 1, missing 1", output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})
            doc = {"fingerprint": name_sheet.fingerprint({"2": groups["2"]}), "clusters": ["2"], "answers": {"2": "invalid"}}
            _, output = self.run_apply(data, doc)
            self.assertIn("unknown 1", output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})
            doc["answers"]["2"] = "S0001"
            self.assertEqual(self.run_apply(data, doc)[0], 0)
            self.assertEqual(name_sheet.load(data / "corrections.json")["faces"]["p1:3"], "S0001")

    def test_create_and_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            doc = {"fingerprint": name_sheet.fingerprint(groups), "answers": {"1": "S10010", "2": "S10010"}}
            before = {p.name: p.read_bytes() for p in data.iterdir()}
            _, output = self.run_apply(data, doc, "--dry-run")
            self.assertIn("applied 2", output)
            self.assertIn("created 0", output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})
            _, output = self.run_apply(data, doc)
            self.assertIn("created 1", output)
            self.assertIn("applied 2", output)
            meta = name_sheet.load(data / "people_meta.json")
            self.assertEqual(meta["S10010"]["batch"], "MB28")
            self.assertEqual(meta["S10010"]["name"], "")


if __name__ == "__main__":
    unittest.main()
