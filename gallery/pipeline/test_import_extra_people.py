import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import import_extra_people as importer
import people_store as store


class ImportTest(unittest.TestCase):
    def test_counts_and_names(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            store.add_person(data, "0001")
            store.add_person(data, "0002", "Example Person")
            source = data / "input.json"
            store.save(source, [{"reg_no": "0001", "name": "New Person"},
                                {"reg_no": "0002", "name": "Other Person"},
                                {"reg_no": "0003", "name": "New Person", "batch": "MB29", "ignored": True},
                                {"reg_no": "bad"}, None])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                importer.main([str(source), "--data", str(data)])
            self.assertEqual(output.getvalue(), "created 1, updated_name 1, unchanged 1, invalid 2\n")
            self.assertTrue(store.get_person(data, "0002")["name"] == "Example Person")
            self.assertEqual(importer.import_people(data, []), dict(created=0, updated_name=0, unchanged=0, invalid=0))


if __name__ == "__main__":
    unittest.main()
