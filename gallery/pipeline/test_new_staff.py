import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import apply_cluster_names
import faces
import name_sheet
import people_store
from test_name_sheet import fixture, write

JS = Path(__file__).parent / "sheet_assets" / "sheet.js"


class AddStaff(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        write(self.data / "people_meta.json", {"STAFF_001": {"id": "STAFF_001", "kind": "staff", "name": "Sri Test Teacher", "role": "Maths"}})

    def test_creates_and_persists(self):
        p = people_store.add_staff(self.data, "  Test   New ")
        self.assertEqual((p["kind"], p["name"], p["id"]), ("staff", "Test New", "STAFF_N_test_new"))
        self.assertEqual(json.loads((self.data / "extra_staff.json").read_text()), [{"id": p["id"], "name": "Test New", "role": ""}])
        self.assertIn(p["id"], json.loads((self.data / "people_meta.json").read_text()))

    def test_same_name_is_reused_even_with_shri_or_brackets(self):
        a = people_store.add_staff(self.data, "Shri Test Teacher (Maths)")
        self.assertEqual(a["id"], "STAFF_001")
        self.assertFalse((self.data / "extra_staff.json").exists())
        b = people_store.add_staff(self.data, "Test New")
        self.assertEqual(people_store.add_staff(self.data, "test new")["id"], b["id"])
        self.assertEqual(len(json.loads((self.data / "extra_staff.json").read_text())), 1)

    def test_validation(self):
        for bad in ("x", "", "a" * 61, "bad\nname", None):
            with self.assertRaises(ValueError):
                people_store.add_staff(self.data, bad)

    def test_load_people_reads_extra_staff(self):
        write(self.data / "extra_staff.json", [{"id": "STAFF_N_test_new", "name": "Test New", "role": ""}])
        hello = self.data / "hello"
        hello.mkdir()
        write(hello / "identities.json", {"students": [], "staff": []})
        old = (faces.DATA, faces.HELLO)
        faces.DATA, faces.HELLO = self.data, hello
        try:
            p = faces.load_people()
        finally:
            faces.DATA, faces.HELLO = old
        self.assertEqual((p["STAFF_N_test_new"]["kind"], p["STAFF_N_test_new"]["portrait_src"]), ("staff", None))


class ApplyStaff(unittest.TestCase):
    def run_apply(self, answers, people):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            path = data.parent / "answers.json"
            write(path, {"fingerprint": name_sheet.fingerprint(groups), "answers": answers, "people": people})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                apply_cluster_names.main([str(path), "--data", str(data)])
            return out.getvalue(), name_sheet.load(data / "people_meta.json"), name_sheet.load(data / "corrections.json")

    def test_staff_answer_creates_person_and_labels_faces(self):
        out, meta, corr = self.run_apply({"1": "STAFF_N_test_new_1a"}, {"STAFF_N_test_new_1a": {"name": "Test New", "kind": "staff"}})
        self.assertIn("created 1", out)
        self.assertEqual(meta["STAFF_N_test_new"]["name"], "Test New")
        self.assertIn("STAFF_N_test_new", corr["faces"].values())

    def test_staff_answer_without_a_name_is_unknown(self):
        out, meta, corr = self.run_apply({"1": "STAFF_N_noname_1a"}, {})
        self.assertIn("unknown 1", out)
        self.assertFalse([k for k in meta if k.startswith("STAFF_N_")])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SheetStaffRow(unittest.TestCase):
    def rows(self, text):
        code = ("const {filterPeople}=require(process.argv[1]);"
                "console.log(JSON.stringify(filterPeople([{id:'S0001',name:'Test One',reg:'0001'}], process.argv[2])))")
        return json.loads(subprocess.run(["node", "-e", code, str(JS), text], capture_output=True, text=True, check=True).stdout)

    def test_staff_prefix_makes_a_staff_row(self):
        row = [r for r in self.rows("staff  Test   New") if r.get("newPerson")][0]
        self.assertEqual((row["kind"], row["newName"]), ("staff", "Test New"))
        self.assertRegex(row["id"], r"^STAFF_N_test_new_[a-z0-9]+$")

    def test_no_staff_row_without_a_name(self):
        self.assertFalse([r for r in self.rows("staff") if r.get("newPerson")])
        self.assertFalse([r for r in self.rows("staff x") if r.get("newPerson")])


if __name__ == "__main__":
    unittest.main()
