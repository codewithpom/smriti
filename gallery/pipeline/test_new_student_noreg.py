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
import merge_people
import name_sheet
import people_store
from test_name_sheet import fixture, write

JS = Path(__file__).parent / "sheet_assets" / "sheet.js"


class NoRegStudent(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        write(self.data / "people_meta.json", {})

    def test_create_persist_and_reuse(self):
        p = people_store.add_student_noreg(self.data, " Test  Kid ", "MB27")
        self.assertEqual((p["kind"], p["name"], p["batch"], p["reg_no"], p["id"]), ("student", "Test Kid", "MB27", "", "STU_N_test_kid"))
        self.assertEqual(json.loads((self.data / "extra_students_noreg.json").read_text()), [{"id": p["id"], "name": "Test Kid", "batch": "MB27"}])
        self.assertEqual(people_store.add_student_noreg(self.data, "test kid", "MB27")["id"], p["id"])
        self.assertNotEqual(people_store.add_student_noreg(self.data, "test kid", "MB28")["id"], p["id"])   # other batch = other person

    def test_validation(self):
        for name, batch in (("x", "MB28"), ("a" * 61, "MB28"), ("bad\nname", "MB28"), ("Test Kid", "XX1")):
            with self.assertRaises(ValueError):
                people_store.add_student_noreg(self.data, name, batch)

    def test_load_people_reads_it_and_alias_hides_it(self):
        write(self.data / "extra_students_noreg.json", [{"id": "STU_N_test_kid", "name": "Test Kid", "batch": "MB27"}])
        hello = self.data / "hello"; hello.mkdir()
        write(hello / "identities.json", {"students": [], "staff": []})
        old = (faces.DATA, faces.HELLO); faces.DATA, faces.HELLO = self.data, hello
        try:
            self.assertEqual(faces.load_people()["STU_N_test_kid"]["batch"], "MB27")
            write(self.data / "manual_aliases.json", {"STU_N_test_kid": "S10123"})
            self.assertNotIn("STU_N_test_kid", faces.load_people())
        finally:
            faces.DATA, faces.HELLO = old

    def test_merge_links_names_and_validates(self):
        p = people_store.add_student_noreg(self.data, "Test Kid", "MB28")
        self.assertEqual(merge_people.merge(self.data, p["id"], "S10123"), {"linked": 1, "named": 1})   # target did not exist: created, then takes the name
        self.assertEqual(json.loads((self.data / "manual_aliases.json").read_text()), {p["id"]: "S10123"})
        self.assertEqual(json.loads((self.data / "people_meta.json").read_text())["S10123"]["name"], "Test Kid")
        for src, dst in (("S10123", "S10124"), (p["id"], "STAFF_001"), (p["id"], "nonsense")):
            with self.assertRaises(ValueError):
                merge_people.merge(self.data, src, dst)


class ApplyNoReg(unittest.TestCase):
    def test_apply_creates_student_with_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            path = data.parent / "answers.json"
            write(path, {"fingerprint": name_sheet.fingerprint(groups), "answers": {"1": "STU_N_test_kid_1a"},
                         "people": {"STU_N_test_kid_1a": {"name": "Test Kid", "kind": "student", "batch": "MB27"}}})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                apply_cluster_names.main([str(path), "--data", str(data)])
            self.assertIn("created 1", out.getvalue())
            p = name_sheet.load(data / "people_meta.json")["STU_N_test_kid"]
            self.assertEqual((p["name"], p["batch"]), ("Test Kid", "MB27"))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class SheetStudentRow(unittest.TestCase):
    def rows(self, text):
        code = ("const {filterPeople}=require(process.argv[1]);"
                "console.log(JSON.stringify(filterPeople([{id:'S0001',name:'Test One',reg:'0001'}], process.argv[2])))")
        return [r for r in json.loads(subprocess.run(["node", "-e", code, str(JS), text], capture_output=True, text=True, check=True).stdout) if r.get("newPerson")]

    def test_student_prefix_with_and_without_batch(self):
        a = self.rows("student  Test  Kid")[0]
        self.assertEqual((a["kind"], a["batch"], a["newName"]), ("student", "MB28", "Test Kid"))
        self.assertRegex(a["id"], r"^STU_N_test_kid_[a-z0-9]+$")
        b = self.rows("student mb27 Test Kid")[0]
        self.assertEqual((b["batch"], b["newName"]), ("MB27", "Test Kid"))

    def test_too_short_has_no_row(self):
        self.assertEqual(self.rows("student"), [])
        self.assertEqual(self.rows("student x"), [])


if __name__ == "__main__":
    unittest.main()
