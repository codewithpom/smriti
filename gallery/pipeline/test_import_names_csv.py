import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import import_names_csv as importer
import people_store as store


class ImportTest(unittest.TestCase):
    def test_counts_sections_and_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            store.add_person(data, "0001")
            store.add_person(data, "0002", "Example Person")
            csv = "REGISTRATION_NO,Full_Name,section,batch\n0001,New Person,A,MB28\n0002,Other Person,B,\n0003,New Person,C,MB29\nbad,Invalid,,\n0004\n"
            counts = importer.import_names(data, io.StringIO(csv))
            self.assertEqual(counts, dict(created=1, named=1, kept_existing=1, invalid=2))
            self.assertTrue(store.get_person(data, "0002")["name"] == "Example Person")
            self.assertEqual(store.get_person(data, "0002")["section"], "B")
            self.assertEqual(store.get_person(data, "0003")["batch"], "MB29")
            counts = importer.import_names(data, io.StringIO("0002,Other Person\n"), overwrite=True)
            self.assertEqual(counts["named"], 1)
            self.assertTrue(store.get_person(data, "0002")["name"] == "Other Person")
            importer.import_names(data, io.StringIO("reg,name,section\n0002,Other Person,Z\n"))
            self.assertEqual(store.get_person(data, "0002")["section"], "B")

    def test_cli_headerless_and_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            source = data / "input.csv"
            source.write_text("0001,Example Person\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                importer.main([str(source), "--data", str(data)])
            self.assertEqual(output.getvalue(), "created 1, named 0, kept_existing 0, invalid 0\n")
            for reg in importer.REG_HEADERS:
                for name in importer.NAME_HEADERS:
                    counts = importer.import_names(data, io.StringIO(f"{reg.upper()},{name.upper()}\n0001,Other Person\n"))
                    self.assertEqual(counts["kept_existing"], 1)
            self.assertEqual(importer.import_names(data, io.StringIO("reg,other\n0001,x\n"))["invalid"], 1)
            self.assertEqual(importer.import_names(data, io.StringIO("0001,x,extra\n"))["invalid"], 1)


if __name__ == "__main__":
    unittest.main()
