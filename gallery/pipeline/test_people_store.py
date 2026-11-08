import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import people_store as store
import review_server


class StoreTest(unittest.TestCase):
    def test_validation(self):
        for reg, name, batch in [(None, "", None), ("123", "", None), ("1234567", "", None),
                                 ("12a4", "", None),
                                 ("0001", "x" * 61, None), ("0001", "x\x00", None),
                                 ("0001", "x\n", None), ("0001", None, None),
                                 ("0001", "", "mb28"), ("0001", "", 28)]:
            with self.assertRaises(ValueError):
                store.validate(reg, name, batch)
        reg, name, batch = store.validate(" 0001 ", "  Example   Person  ", "MB29")
        self.assertTrue(reg == "0001" and name == "Example Person" and batch == "MB29")

    def test_defaults(self):
        for reg, expected in [(10009, "MB27"), (10010, "MB28"), (10335, "MB28"), (10336, "MB27")]:
            self.assertTrue(store.validate(str(reg))[2] == expected)

    def test_create_fill_and_no_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            store.save(data / "people.json", [])
            person = store.add_person(data, "0001")
            self.assertEqual(set(person), {"id", "kind", "name", "reg_no", "batch", "section"})
            store.add_person(data, "0001", "Example Person")
            store.add_person(data, "0001", "Another Person")
            extra = store.load(data / "extra_students.json", [])
            meta = store.load(data / "people_meta.json", {})
            people = store.load(data / "people.json", [])
            self.assertEqual((len(extra), len(meta), len(people)), (1, 1, 1))
            self.assertTrue(extra[0]["name"] == meta[person["id"]]["name"] == people[0]["name"] == "Example Person")
            self.assertEqual((people[0]["photos"], people[0]["avatar"], people[0]["community"], people[0]["friends"]), (0, None, None, []))
            self.assertFalse(list(data.glob("*.tmp")))

    def test_extra_only_and_meta_only(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            store.save(data / "extra_students.json", [{"reg_no": "0001", "name": "", "section": "A", "batch": "MB29"}])
            person = store.add_person(data, "0001", "Example Person")
            self.assertTrue(person["name"] == "Example Person" and person["section"] == "A" and person["batch"] == "MB29")
            self.assertEqual(len(store.load(data / "people_meta.json", {})), 1)
            store.save(data / "extra_students.json", [])
            person["name"] = "Reg " + person["reg_no"]
            store.save(data / "people_meta.json", {person["id"]: person})
            before = (data / "people_meta.json").read_bytes()
            returned = store.add_person(data, "0001", "Example Person")
            self.assertTrue(returned["name"] == "Example Person")
            self.assertEqual((data / "people_meta.json").read_bytes(), before)
            self.assertEqual(store.load(data / "extra_students.json", []), [])

    def test_atomic_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "file.json"
            store.save(path, {"count": 1})
            with patch.object(store.os, "replace", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    store.save(path, {"count": 2})
            self.assertEqual(store.load(path, {}), {"count": 1})
            self.assertFalse(list(Path(d).glob("*.tmp")))

    def test_real_metadata_name_survives_empty_extra_entry(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            person = store.add_person(data, "0001", "Example Person")
            extra = store.load(data / "extra_students.json", [])
            extra[0]["name"] = ""
            store.save(data / "extra_students.json", extra)
            returned = store.add_person(data, "0001", "Other Person")
            self.assertTrue(returned["name"] == person["name"])


class ServerTest(unittest.TestCase):
    def test_endpoint_and_answer(self):
        class QuietHandler(review_server.H):
            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as d, patch.object(review_server, "DATA", Path(d)):
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                for body, expected in [({"reg_no": "0001"}, 200), ({"reg_no": "bad"}, 400), ([], 400)]:
                    connection.request("POST", "/api/addperson", json.dumps(body), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, expected)
                    self.assertEqual(result["ok"], expected == 200)
                connection.close()
                store.save(Path(d) / "questions.json", [{"id": "c1", "type": "cluster", "faces": ["p:0"]}])
                code, result = review_server.answer({"qid": "c1", "type": "cluster", "person": "S0001"})
                self.assertTrue(code == 200 and result["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
