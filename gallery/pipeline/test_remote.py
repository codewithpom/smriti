import base64
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import urlparse, parse_qs, unquote

from remote import (seed, Budget, FirebaseStorageRemote, KVRemote, LocalRemote,
                    QuotaDeferred, R2Remote, migrate, open_remote)


class Response:
    status = 200
    def __init__(self, body=b""):
        self.body = body
    def read(self):
        return self.body


class FakeKV:
    def __init__(self):
        self.values, self.metadata, self.requests = {}, {}, []
        self.fail429 = 0
    def __call__(self, request):
        self.requests.append(request)
        path = urlparse(request.full_url).path
        method = request.method
        if self.fail429:
            self.fail429 -= 1
            raise HTTPError(request.full_url, 429, "slow", {}, None)
        if path.endswith("/bulk") and method == "PUT":
            for row in json.loads(request.data):
                self.values[row["key"]] = base64.b64decode(row["value"])
                self.metadata[row["key"]] = row["metadata"]
            return Response(b'{"success":true}')
        if path.endswith("/bulk/delete"):
            for key in json.loads(request.data): self.values.pop(key, None)
            return Response(b'{"success":true}')
        if "/values/" in path:
            from urllib.parse import unquote
            key = unquote(path.split("/values/", 1)[1])
            if method == "GET":
                if key not in self.values: raise HTTPError(request.full_url, 404, "missing", {}, None)
                return Response(self.values[key])
        if path.endswith("/keys"):
            query = parse_qs(urlparse(request.full_url).query)
            prefix = query.get("prefix", [""])[0]
            rows = [{"name": k, "metadata": self.metadata[k]} for k in sorted(self.values) if k.startswith(prefix)]
            return Response(json.dumps({"result": rows, "result_info": {"count": len(rows)}}).encode())
        raise AssertionError((method, path))


class FakeGCSResponse:
    def __init__(self, status_code=200, content=b"", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload
    def json(self):
        return self._payload
    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


class FakeGCS:
    """Small JSON API fake; all operations stay in process and touch no cloud."""
    def __init__(self):
        self.objects = {}
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        parsed = urlparse(url)
        params = dict(kwargs.get("params") or {})
        path = parsed.path
        if method == "POST" and "/upload/storage/" in path:
            name = params["name"]
            body = kwargs["data"]
            payload = body.read() if hasattr(body, "read") else body
            self.objects[name] = (payload, kwargs["headers"]["Content-Type"])
            return FakeGCSResponse(payload={"name": name})
        if method == "GET" and path.endswith("/o"):
            prefix = params.get("prefix", "")
            rows = [{"name": name, "size": str(len(data))}
                    for name, (data, _) in sorted(self.objects.items()) if name.startswith(prefix)]
            start = int(params.get("pageToken", 0))
            page = rows[start:start + 2]
            next_token = str(start + 2) if start + 2 < len(rows) else None
            payload = {"items": page}
            if next_token:
                payload["nextPageToken"] = next_token
            return FakeGCSResponse(payload=payload)
        if "/o/" in path:
            name = unquote(path.split("/o/", 1)[1])
            if method == "GET":
                if name not in self.objects:
                    return FakeGCSResponse(404)
                return FakeGCSResponse(content=self.objects[name][0])
            if method == "DELETE":
                if name not in self.objects:
                    return FakeGCSResponse(404)
                del self.objects[name]
                return FakeGCSResponse(204)
        raise AssertionError((method, path, kwargs))


class RemoteTests(unittest.TestCase):
    def test_local(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); local = LocalRemote(root / "remote")
            source = root / "src"; source.mkdir(); (source / "a.txt").write_bytes(b"abc")
            self.assertEqual(local.push_dir(source, "state"), 1)
            self.assertEqual(local.push_dir(source, "state"), 0)
            self.assertEqual(local.get("state/a.txt"), b"abc")
            self.assertEqual(local.ls("state/")[0], ("state/a.txt", 3))
            self.assertTrue(local.pack_put("state/archive.tar.gz", source))
            out = root / "out"
            self.assertTrue(local.unpack_get("state/archive.tar.gz", out))
            self.assertEqual((out / "a.txt").read_bytes(), b"abc")

    def test_kv_metadata_batch_404_retry_chunk_and_delete(self):
        fake = FakeKV(); sleeps = []
        kv = KVRemote("acct", "ns", "secret", fake, sleep=sleeps.append)
        fake.fail429 = 1
        kv.put("folder/a file", b"abc", "text/plain")
        row = json.loads(fake.requests[-1].data)[0]
        self.assertEqual(row, {"key": "folder/a file", "value": "YWJj", "base64": True,
                               "metadata": {"httpMetadata": {"contentType": "text/plain"}, "size": 3}})
        self.assertTrue(any("folder%2Fa%20file" in request.full_url for request in fake.requests))
        self.assertEqual(sleeps, [1])
        self.assertIsNone(kv.get("missing"))
        self.assertEqual(kv.get("folder/a file"), b"abc")
        self.assertEqual(kv.ls("folder/"), [("folder/a file", 3)])
        huge = b"z" * (25 * 1024 * 1024)
        kv.put("big", huge)
        self.assertEqual(kv.get("big"), huge)
        self.assertEqual(kv.ls("")[-1], ("folder/a file", 3))
        kv.delete([f"k{i}" for i in range(1001)])
        self.assertEqual([len(json.loads(r.data)) for r in fake.requests if r.full_url.endswith("/bulk/delete")], [1000, 1])

    def test_bulk_batches(self):
        fake = FakeKV(); kv = KVRemote("a", "n", "t", fake)
        kv.put_many([(f"k{i}", b"x", None) for i in range(205)])
        batches = [json.loads(r.data) for r in fake.requests if r.full_url.endswith("/bulk")]
        self.assertEqual([len(b) for b in batches], [100, 100, 5])

    def test_budget(self):
        fake = FakeKV(); kv = KVRemote("a", "n", "t", fake)
        budget = Budget(kv, {"writes": 1, "deletes": 1, "lists": 1}, today=lambda: "2026-01-01")
        kv.budget = budget
        kv.put("x", b"x")
        before = len(fake.requests)
        with self.assertRaises(QuotaDeferred): kv.put("y", b"y")
        self.assertEqual(len(fake.requests), before)
        budget.save()
        stored = json.loads(fake.values["state/kv_budget.json"])
        self.assertEqual(stored, {"date": "2026-01-01", "writes": 1, "deletes": 0, "lists": 0})
        # a bulk call is billed per key: 3 keys against a budget of 2 sends nothing
        kv.budget = Budget(kv, {"writes": 2, "deletes": 1, "lists": 1}, today=lambda: "2026-01-03")
        before = len(fake.requests)
        with self.assertRaises(QuotaDeferred): kv.put_many([("a", b"1", None), ("b", b"2", None), ("c", b"3", None)])
        self.assertEqual(len(fake.requests), before)
        fresh = Budget(kv, today=lambda: "2026-01-02")
        self.assertEqual(fresh.state, {"date": "2026-01-02", "writes": 0, "deletes": 0, "lists": 0})

    def test_r2_commands(self):
        seen = []
        def run(argv, **kwargs):
            seen.append(argv)
            class Result: returncode = 0; stdout = b'[{"Path":"web/x.jpg","Name":"x.jpg","Size":7}]'
            return Result()
        remote = R2Remote("bucket", run)
        self.assertEqual(remote.ls("img"), [("img/web/x.jpg", 7)])   # recursive listing keeps subfolders
        self.assertEqual(seen[0], ["rclone", "lsjson", "-R", "--files-only", "r2:bucket/img", "--log-level", "ERROR"])

    def test_firebase_json_api_object_and_directory_contract(self):
        fake = FakeGCS()
        remote = FirebaseStorageRemote("gallery-bucket", session=fake)
        remote.put("img/photos/a b.webp", b"photo", "image/webp")
        remote.put("img/photos/b.webp", b"photo2", "image/webp")
        remote.put("img/photos/c.webp", b"photo3", "image/webp")
        remote.put("state/catalog.json", b"[]", "application/json")
        self.assertEqual(remote.get("img/photos/a b.webp"), b"photo")
        self.assertIsNone(remote.get("missing"))
        self.assertEqual(remote.ls("img/"), [
            ("img/photos/a b.webp", 5), ("img/photos/b.webp", 6), ("img/photos/c.webp", 6),
        ])
        self.assertTrue(remote.originals)
        upload = next(row for row in fake.requests if row[0] == "POST")
        self.assertEqual(upload[2]["headers"]["Content-Type"], "image/webp")
        self.assertEqual(fake.objects["img/photos/a b.webp"][1], "image/webp")
        self.assertEqual(dict(parse_qs(urlparse(upload[1]).query)), {})
        self.assertEqual(upload[2]["params"], {"uploadType": "media", "name": "img/photos/a b.webp"})

        with tempfile.TemporaryDirectory() as td:
            pulled = Path(td) / "pulled"
            self.assertEqual(remote.pull_dir("img/", pulled), 3)
            self.assertEqual((pulled / "photos" / "a b.webp").read_bytes(), b"photo")
            source = Path(td) / "new"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "x.txt").write_text("original")
            self.assertEqual(remote.push_dir(source, "originals/uploads", only_changed=False), 1)
            self.assertEqual(remote.get("originals/uploads/nested/x.txt"), b"original")
            packed = Path(td) / "packed"
            self.assertTrue(remote.pack_put("state/archive.tar.gz", source))
            self.assertTrue(remote.unpack_get("state/archive.tar.gz", packed))
            self.assertEqual((packed / "nested" / "x.txt").read_text(), "original")

        remote.delete(["img/photos/a b.webp", "missing"])
        self.assertIsNone(remote.get("img/photos/a b.webp"))

    def test_firebase_large_object_is_one_gcs_object_not_kv_chunks(self):
        fake = FakeGCS()
        remote = FirebaseStorageRemote("gallery-bucket", session=fake)
        payload = b"x" * (25 * 1024 * 1024 + 1)
        remote.put("originals/uploads/large.jpg", payload, "image/jpeg")
        uploads = [row for row in fake.requests if row[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(fake.objects["originals/uploads/large.jpg"], (payload, "image/jpeg"))
        self.assertEqual(remote.ls("originals/uploads/"), [("originals/uploads/large.jpg", len(payload))])
        self.assertFalse(any(".~part" in name for name in fake.objects))

    def test_firebase_migration_copies_without_touching_source(self):
        with tempfile.TemporaryDirectory() as td:
            source = LocalRemote(Path(td) / "source")
            source.put("data/photos.json", b"[]")
            source.put("originals/uploads/1.jpg", b"original")
            target = FirebaseStorageRemote("bucket", session=FakeGCS())
            result = migrate(source, target)
            self.assertEqual(result, {"copied": 2, "skipped": 0, "deferred": False, "verified": True})
            self.assertEqual(source.get("data/photos.json"), b"[]")
            self.assertEqual(source.get("originals/uploads/1.jpg"), b"original")

    def test_open_firebase_requires_bucket_and_credentials_file_without_echoing_values(self):
        with self.assertRaises(SystemExit) as caught:
            open_remote({"STORAGE_BACKEND": "firebase", "FIREBASE_STORAGE_BUCKET": "bucket",
                         "GOOGLE_APPLICATION_CREDENTIALS": "not-a-real-secret-path"})
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS file", str(caught.exception))
        self.assertNotIn("not-a-real-secret-path", str(caught.exception))
        with tempfile.TemporaryDirectory() as td:
            credential_path = Path(td) / "service-account.json"
            credential_path.write_text("{}")
            env = {"STORAGE_BACKEND": "firebase", "FIREBASE_STORAGE_BUCKET": "gallery-bucket",
                   "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path)}
            from unittest.mock import patch
            with patch("remote.FirebaseStorageRemote") as constructor:
                open_remote(env)
            constructor.assert_called_once_with("gallery-bucket", credential_path)

    def test_migrate_deferred_resume(self):
        with tempfile.TemporaryDirectory() as td:
            src = LocalRemote(Path(td) / "src"); dst = LocalRemote(Path(td) / "dst")
            src.put("data/a", b"a"); src.put("originals/b", b"b")
            class FakeKVLocal(LocalRemote):
                backend = "kv"
            kv_dst = FakeKVLocal(Path(td) / "dst-kv")
            result = migrate(src, kv_dst)
            self.assertEqual(result, {"copied": 1, "skipped": 1, "deferred": False, "verified": True})
            class Deferred(LocalRemote):
                def put(self, *args, **kwargs): raise QuotaDeferred()
            blocked = Deferred(Path(td) / "blocked")
            self.assertTrue(migrate(src, blocked)["deferred"])
            self.assertTrue(migrate(src, kv_dst)["verified"])

    def test_seed_uses_renamed_site_and_keeps_subfolders(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, site = root / "data", root / "site"
            (data / "extra_refs").mkdir(parents=True); (data / "fx").mkdir(); (site / "data").mkdir(parents=True); (site / "img").mkdir()
            (data / "catalog.json").write_text("[]"); (data / "extra_refs" / "1.jpg").write_bytes(b"r")
            (data / "fx" / "p1.json").write_text("[]"); (data / "photos.json").write_text('[{"thumb":"img/thumb/p1.webp"}]')
            (site / "data" / "photos.json").write_text('[{"thumb":"img/abc.webp"}]'); (site / "img" / "abc.webp").write_bytes(b"i")
            store = LocalRemote(root / "remote")
            seed(store, data, site)
            self.assertEqual(store.get("state/extra_refs/1.jpg"), b"r")
            self.assertIsNotNone(store.get("state/fx.tar.gz"))
            self.assertEqual(store.get_json("data/photos.json"), [{"thumb": "img/abc.webp"}])   # renamed copy, not the plain one
            self.assertEqual(store.get("img/abc.webp"), b"i")
            self.assertIsNotNone(store.get("state/manifest/img.json"))   # first CI push then uploads only new images

    def test_missing_settings_do_not_echo_values(self):
        with self.assertRaises(SystemExit) as caught: open_remote({"STORAGE_BACKEND": "kv", "CLOUDFLARE_API_TOKEN": "secret-value"})
        self.assertIn("CF_KV_NAMESPACE_ID", str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
