"""Offline coverage for the incremental scanner."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import ci_scan
import dedupe
import remote


class FakeFaces:
    @staticmethod
    def extract(reembed=False):
        path = ci_scan.DATA / "faces.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        catalog = json.loads((ci_scan.DATA / "catalog.json").read_text())
        for entry in catalog:
            if entry.get("pending"):
                current.setdefault(entry["id"], [])
        path.write_text(json.dumps(current))

    @staticmethod
    def match():
        return None


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "remote"
        self.local = remote.LocalRemote(self.store)
        self.data = self.root / "gallery" / "data"
        self.gallery = self.root / "gallery"
        self.sorted = self.root / "sorted"
        self.people = self.root / "people"
        self.data.mkdir(parents=True)
        self.people.mkdir()
        self.local.put("people/identities.json", b'{"people":[]}')
        self.local.put("state/people_meta.json", b'{}')
        self.local.put("data/photos.json", b'[]')
        entries = []
        for idx, color in enumerate(((200, 20, 30), (15, 170, 40))):
            path = self.root / f"published{idx}.jpg"
            Image.new("RGB", (64, 48), color).save(path, "JPEG", quality=92)
            info = dedupe.image_info(path)
            entries.append({"id": f"p{idx:08x}", "file": f"old/{idx}.jpg", "name": f"{idx}.jpg", "scene": "old",
                            "date": None, "width": info["w"], "height": info["h"], "orientation": "landscape", "has_gps": False,
                            "sha": info["sha"], "ph": info["ph"], "color": "#888888"})
            (self.data / f"seed{idx}.jpg").write_bytes(path.read_bytes())
        self.local.put("state/catalog.json", json.dumps(entries).encode())
        self.local.put("data/people.json", b'[]')
        self.local.put("data/graph.json", b'{"nodes":[],"edges":[]}')
        self.local.put("data/questions.json", b'[]')
        self.local.put("data/clusters.json", b'[]')
        self.local.put("state/people_meta.json", b'{}')
        # New synthetic image differs from both published images.
        self.new = self.root / "new.jpg"
        im = Image.new("RGB", (80, 60))
        for y in range(60):
            for x in range(80):
                im.putpixel((x, y), ((x * 3) % 256, (y * 4) % 256, ((x + y) * 2) % 256))
        im.save(self.new, "JPEG", quality=92)
        raw = self.new.read_bytes()
        self.upload_keys = ["pending/user-a/1-a.jpg", "pending/user-a/2-b.jpg", "pending/user-b/3-c.jpg"]
        self.local.put(self.upload_keys[0], raw)
        self.local.put(self.upload_keys[1], (self.root / "published0.jpg").read_bytes())
        with Image.open(self.new) as source:
            source.resize((40, 30)).save(self.root / "resized.jpg", "JPEG", quality=65)
        self.local.put(self.upload_keys[2], (self.root / "resized.jpg").read_bytes())
        self.old_data, self.old_gallery, self.old_sorted, self.old_people = ci_scan.DATA, ci_scan.GALLERY, ci_scan.SRC, ci_scan.PEOPLE
        ci_scan.DATA, ci_scan.GALLERY, ci_scan.SRC, ci_scan.PEOPLE = self.data, self.gallery, self.sorted, self.people
        self.old_module_paths = (ci_scan.build.DATA, ci_scan.assets.GALLERY, ci_scan.assets.SRC,
                                 ci_scan.assets.IMG, ci_scan.assets.WEB_DIR, ci_scan.assets.THUMB_DIR,
                                 ci_scan.catalog_mod.OUT)
        ci_scan.build.DATA = self.data
        ci_scan.assets.GALLERY = self.gallery
        ci_scan.assets.SRC = self.sorted
        ci_scan.assets.IMG = self.gallery / "img"
        ci_scan.assets.WEB_DIR = self.gallery / "img" / "web"
        ci_scan.assets.THUMB_DIR = self.gallery / "img" / "thumb"
        ci_scan.catalog_mod.OUT = self.data / "catalog.json"
        ci_scan.run_faces = FakeFaces
        self.env = patch.dict(os.environ, {"STORAGE_BACKEND": "local", "STORAGE_LOCAL_DIR": str(self.store), "GITHUB_OUTPUT": str(self.root / "output"), "IMG_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        ci_scan.DATA, ci_scan.GALLERY, ci_scan.SRC, ci_scan.PEOPLE = self.old_data, self.old_gallery, self.old_sorted, self.old_people
        (ci_scan.build.DATA, ci_scan.assets.GALLERY, ci_scan.assets.SRC,
         ci_scan.assets.IMG, ci_scan.assets.WEB_DIR, ci_scan.assets.THUMB_DIR,
         ci_scan.catalog_mod.OUT) = self.old_module_paths
        self.tmp.cleanup()

    def test_pending_duplicate_approval_rejection_and_safe_logs(self):
        with patch.object(ci_scan, "open_remote", return_value=self.local), patch("sys.stdout", new_callable=__import__("io").StringIO) as output:
            self.assertEqual(ci_scan.scan(type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})()), 0)
        log = output.getvalue()
        for secret in (*self.upload_keys, "user-a", "user-b", "p00000000"):
            self.assertNotIn(secret, log)
        statuses_a = self.local.get_json("status/uploads/user-a.json")["items"]
        statuses_b = self.local.get_json("status/uploads/user-b.json")["items"]
        self.assertEqual({row["state"] for row in statuses_a}, {"awaiting_approval", "duplicate"})
        self.assertEqual(statuses_b[0]["state"], "duplicate")
        self.assertTrue(statuses_a[1].get("dupOf"))
        catalog = self.local.get_json("state/catalog.json")
        self.assertEqual(sum(bool(e.get("pending")) for e in catalog), 1)
        self.assertEqual(len(self.local.get_json("data/photos.json")), 2)
        self.assertEqual(len(self.local.get_json("data/pending.json")["items"]), 1)
        self.assertEqual(self.local.ls("pending/"), [])
        self.assertEqual(self.local.get_json("status/scan.json")["state"], "done")
        self.assertIn("publish=true", Path(os.environ["GITHUB_OUTPUT"]).read_text())
        entry = next(e for e in catalog if e.get("pending"))
        self.local.put_json(f"review/{entry['id']}.json", {"action": "approve", "by": "admin", "at": "now"})
        with patch.object(ci_scan, "open_remote", return_value=self.local):
            ci_scan.scan(type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})())
        self.assertEqual(len(self.local.get_json("data/photos.json")), 3)
        self.assertIsNone(self.local.get(f"review/{entry['id']}.json"))
        self.assertIn("live", {x["state"] for x in self.local.get_json("status/uploads/user-a.json")["items"]})

    def test_failure_leaves_pending_and_marks_failed(self):
        key = "pending/user-a/one.jpg"
        self.local.put(key, self.new.read_bytes())
        with patch.object(ci_scan, "open_remote", return_value=self.local), patch.object(ci_scan, "_pull", side_effect=RuntimeError("secret")):
            self.assertEqual(ci_scan.scan(type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})()), 1)
        self.assertIn((key, len(self.new.read_bytes())), self.local.ls("pending/"))
        self.assertEqual(self.local.get_json("status/scan.json")["state"], "failed")

    def test_unseeded_archive_is_never_replaced(self):
        published = [{"id": "p00000000"}, {"id": "p00000001"}]
        self.local.put_json("data/photos.json", published)
        args = type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})()
        original_catalog = self.local.get("state/catalog.json")
        for catalog in (None, b"[]", json.dumps([{"id": "p00000000"}]).encode()):
            with self.subTest(catalog=catalog):
                if catalog is None:
                    self.local.delete(["state/catalog.json"])
                else:
                    self.local.put("state/catalog.json", catalog)
                with patch.object(ci_scan, "_pull") as pull:
                    self.assertEqual(ci_scan.scan(args, self.local), 1)
                pull.assert_not_called()
                self.assertEqual(self.local.get_json("data/photos.json"), published)
                self.assertEqual(len(self.local.ls("pending/")), 3)
                self.assertFalse((self.root / "output").exists())
        self.local.put("state/catalog.json", original_catalog)
        self.assertEqual(ci_scan.scan(args, self.local), 0)

    def test_reject_removes_pending_photo_and_assets(self):
        key = "pending/user-a/reject.jpg"
        self.local.put(key, self.new.read_bytes())
        args = type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})()
        with patch.object(ci_scan, "open_remote", return_value=self.local):
            self.assertEqual(ci_scan.scan(args), 0)
            entry = next(e for e in self.local.get_json("state/catalog.json") if e.get("pending"))
            self.local.put_json(f"review/{entry['id']}.json", {"action": "reject", "by": "admin", "at": "now"})
            self.assertEqual(ci_scan.scan(args), 0)
        self.assertFalse(any(e["id"] == entry["id"] for e in self.local.get_json("state/catalog.json")))
        self.assertIsNone(self.local.get(f"review/{entry['id']}.json"))
        self.assertIn("rejected", {row["state"] for row in self.local.get_json("status/uploads/user-a.json")["items"]})

    def test_obfuscated_run_uploads_renamed_data(self):
        args = type("Args", (), {"eval": False, "recluster": False, "reembed": False, "fetch_facebook": False})()
        with patch.dict(os.environ, {"IMG_KEY": "k" * 64}), patch.object(ci_scan, "open_remote", return_value=self.local):
            self.assertEqual(ci_scan.scan(args), 0)
        # served JSON must point at the renamed images, and only renamed images are stored
        paths = [p["thumb"] for p in self.local.get_json("data/photos.json")] + [i["thumb"] for i in self.local.get_json("data/pending.json")["items"]]
        self.assertTrue(paths and all(p.startswith("img/") and "/thumb/" not in p for p in paths))
        self.assertTrue(all("/thumb/" not in key and "/web/" not in key for key, _ in self.local.ls("img/")))
        self.assertFalse((ci_scan.PIPE / ".secrets" / "img-key.txt").exists())

    def test_publish_never_ships_data_and_binds_storage(self):
        for name in ("css", "js", "functions/api", "functions/tests"):
            (self.gallery / name).mkdir(parents=True, exist_ok=True)
        (self.gallery / "index.html").write_text("<!doctype html>")
        (self.gallery / "functions" / "api" / "x.js").write_text("export {}")
        (self.gallery / "functions" / "tests" / "x.test.mjs").write_text("")
        self.local.put("img/abc.webp", b"img")
        env = {"PAGES_PROJECT": "site", "FIREBASE_PROJECT_ID": "proj-1", "GITHUB_REPOSITORY": "o/r", "STORAGE_BACKEND": "kv", "CF_KV_NAMESPACE_ID": "ns1",
               "SMRITI_IMG_CACHE": str(self.root / "cache")}
        dest = self.root / "site"
        with patch.dict(os.environ, env):
            self.assertEqual(ci_scan.publish(self.local, dest), 1)
        self.assertFalse((dest / "public" / "data").exists())   # data JSON names students: Function-only
        self.assertTrue((dest / "public" / "img" / "abc.webp").exists())
        self.assertTrue((dest / "functions" / "api" / "x.js").exists())
        self.assertFalse((dest / "functions" / "tests").exists())
        self.assertIn('id = "ns1"', (dest / "wrangler.toml").read_text())
        r2 = ci_scan.pages_config({**env, "STORAGE_BACKEND": "r2", "R2_BUCKET": "bucket"})
        self.assertIn('bucket_name = "bucket"', r2)
        with self.assertRaises(SystemExit):
            ci_scan.pages_config({**env, "STORAGE_BACKEND": "kv", "CF_KV_NAMESPACE_ID": 'x"\n[evil]'})

    def test_scheduled_run_without_storage_settings_exits_cleanly(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "", "GITHUB_EVENT_NAME": "schedule"}):
            self.assertEqual(ci_scan.main([]), 0)
        with patch.dict(os.environ, {"STORAGE_BACKEND": "", "GITHUB_EVENT_NAME": "workflow_dispatch"}):
            with self.assertRaises(SystemExit):
                ci_scan.main([])

    def test_packed_recovery_images_and_portraits(self):
        baseline = self.root / "baseline"
        baseline.mkdir()
        (baseline / "old.webp").write_bytes(b"baseline")
        self.local.pack_put("state/published-assets.tar.gz", baseline)
        self.local.put_json("state/published-assets.json", {"sha256": "version1", "paths": ["old.webp"]})
        self.local.put("img/new.webp", b"new")
        cache, dest = self.root / "cache", self.root / "published"
        self.assertEqual(ci_scan._copy_missing_images(self.local, cache, dest), 2)
        self.assertEqual((dest / "old.webp").read_bytes(), b"baseline")
        self.assertEqual((dest / "new.webp").read_bytes(), b"new")
        # A previously cached incremental asset removed from storage stays removed.
        self.local.delete(["img/new.webp"])
        dest2 = self.root / "published2"
        self.assertEqual(ci_scan._copy_missing_images(self.local, cache, dest2), 1)
        self.assertFalse((dest2 / "new.webp").exists())
        portraits = self.root / "packed-portraits"
        portraits.mkdir()
        (portraits / "identities.json").write_text('{"students":[],"staff":[]}')
        (portraits / "portrait.jpg").write_bytes(b"portrait")
        self.local.pack_put("state/people.tar.gz", portraits)
        ci_scan._pull(self.local)
        self.assertEqual((self.people / "portrait.jpg").read_bytes(), b"portrait")
        self.assertEqual(json.loads((self.people / "identities.json").read_text()), {"people": []})
        # Individual people/ files intentionally override the packed baseline.
        self.local.put_json("state/published-assets.json", {"sha256": "bad", "paths": ["../escape.webp"]})
        with self.assertRaises(ValueError):
            ci_scan._copy_missing_images(self.local, cache, dest2)

    def test_baseline_manifest_blocks_stale_overlay_but_allows_updates(self):
        baseline = self.root / "baseline-overlay"
        baseline.mkdir()
        expected = b"canonical-avatar-01"
        stale = b"stale-avatar-------"
        self.assertEqual(len(stale), len(expected))
        (baseline / "avatar.webp").write_bytes(expected)
        version = hashlib.sha256(b"baseline fixture").hexdigest()
        self.local.pack_put("state/published-assets.tar.gz", baseline)
        self.local.put_json("state/published-assets.json", {"sha256": version, "paths": ["avatar.webp"]})
        self.local.put_json("state/manifest/img.json", {"avatar.webp": hashlib.sha1(expected).hexdigest()})
        self.local.put("img/avatar.webp", stale)

        cache, dest = self.root / "baseline-cache", self.root / "baseline-published"
        ci_scan._copy_missing_images(self.local, cache, dest)
        self.assertEqual((dest / "avatar.webp").read_bytes(), expected)
        self.assertEqual((cache / "avatar.webp").read_bytes(), expected)

        updated = b"updated-avatar-bytes"
        self.local.put("img/avatar.webp", updated)
        self.local.put_json("state/manifest/img.json", {"avatar.webp": hashlib.sha1(updated).hexdigest()})
        dest2 = self.root / "baseline-published-updated"
        ci_scan._copy_missing_images(self.local, cache, dest2)
        self.assertEqual((dest2 / "avatar.webp").read_bytes(), updated)

    def test_backfill_marks_duplicate(self):
        source = self.root / "albums"
        album = source / "same"
        album.mkdir(parents=True)
        a = Image.new("RGB", (42, 35), (89, 32, 170))
        a.save(album / "one.jpg", "JPEG")
        a.save(album / "two.jpg", "JPEG")
        entries = [{"id": "p11111111", "file": "same/one.jpg", "name": "one.jpg", "width": 42, "height": 35, "date": "2020-01-01"},
                   {"id": "p22222222", "file": "same/two.jpg", "name": "two.jpg", "width": 42, "height": 35, "date": "2020-01-01"}]
        (self.data / "catalog.json").write_text(json.dumps(entries))
        self.assertEqual(ci_scan.backfill_hashes(source), 0)
        result = json.loads((self.data / "catalog.json").read_text())
        self.assertTrue(any(e.get("dup_of") for e in result))


if __name__ == "__main__":
    unittest.main()
