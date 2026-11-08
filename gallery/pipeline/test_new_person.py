import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import apply_cluster_names
import name_sheet
from test_name_sheet import fixture, write

JS = Path(__file__).parent / "sheet_assets" / "sheet.js"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TypedNewPerson(unittest.TestCase):
    def rows(self, text):
        code = ("const {filterPeople}=require(process.argv[1]);"
                "const people=[{id:'S0001',name:'Test One',reg:'0001'}];"
                "console.log(JSON.stringify(filterPeople(people, process.argv[2])))")
        out = subprocess.run(["node", "-e", code, str(JS), text], capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_reg_alone_and_with_name(self):
        self.assertEqual([r["newName"] for r in self.rows("S10123") if r.get("newPerson")], [""])
        row = [r for r in self.rows("10123  Test   Two") if r.get("newPerson")][0]
        self.assertEqual((row["id"], row["newName"]), ("S10123", "Test Two"))
        self.assertIn("Test Two", row["name"])

    def test_no_new_row_for_existing_or_overlong_name(self):
        self.assertFalse([r for r in self.rows("S0001") if r.get("newPerson")])      # id exists (also 4 digits)
        self.assertFalse([r for r in self.rows("10123 " + "x" * 61) if r.get("newPerson")])
        self.assertFalse([r for r in self.rows("abc") if r.get("newPerson")])


class ApplyNames(unittest.TestCase):
    def test_name_from_sheet_is_used_for_a_created_person(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            groups = name_sheet.cluster_members(name_sheet.load(data / "faces.json"))
            doc = {"fingerprint": name_sheet.fingerprint(groups), "answers": {"1": "S10010", "2": "S10011"},
                   "people": {"S10010": {"name": "Test Two"}}}
            path = data.parent / "answers.json"
            write(path, doc)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(apply_cluster_names.main([str(path), "--data", str(data)]), 0)
            meta = name_sheet.load(data / "people_meta.json")
            self.assertEqual(meta["S10010"]["name"], "Test Two")
            self.assertEqual(meta["S10011"]["name"], "")


if __name__ == "__main__":
    unittest.main()
