import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import ingest_facebook


class ImportImagesTest(unittest.TestCase):
    def test_formats_metadata_orientation_duplicates_and_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            src, dest = Path(d, "src"), Path(d, "dest")
            nested = src / "private album"
            nested.mkdir(parents=True)
            exif = Image.Exif()
            exif[34853] = {1: "N", 2: (12.0, 34.0, 56.0)}
            exif[274] = 6
            jpg = nested / "private title.jpg"
            Image.new("RGB", (8, 4), "red").save(jpg, exif=exif)
            with Image.open(jpg) as original:
                self.assertIn(34853, original.getexif())
            raw = jpg.read_bytes()
            (src / "duplicate.jpeg").write_bytes(raw)
            Image.new("RGBA", (5, 3), (0, 255, 0, 100)).save(src / "photo.PNG")
            Image.new("RGB", (6, 2), "blue").save(src / "photo.webp")
            (src / "broken.jpg").write_bytes(b"not an image")
            (src / "movie.mp4").write_bytes(b"video")
            (src / "notes.txt").write_text("ignored")
            self.assertEqual(ingest_facebook.import_images(src, dest), (3, 1, 1))
            outputs = list((dest / "facebook").iterdir())
            self.assertEqual(len(outputs), 3)
            for output in outputs:
                with Image.open(output) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertFalse(image.getexif())
                    self.assertNotIn("exif", image.info)
            target = dest / "facebook" / f"fb-{hashlib.sha1(raw).hexdigest()[:12]}.jpg"
            with Image.open(target) as image:
                self.assertEqual(image.size, (4, 8))
            before = target.read_bytes()
            self.assertEqual(ingest_facebook.import_images(src, dest), (0, 4, 1))
            self.assertEqual(target.read_bytes(), before)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cookies = self.root / "private cookies.txt"
        self.cookies.write_text("private cookie contents")
        self.url = "https://www.facebook.com/private-album/photos"
        self.args = ["--url", self.url, "--cookies", str(self.cookies),
                     "--dest", str(self.root / "out"), "--archive", str(self.root / "archive")]

    def invoke(self, runner, extra=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ingest_facebook, "run_gallery_dl", side_effect=runner) as mocked:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = ingest_facebook.main(self.args + list(extra))
        for secret in (str(self.root), self.url, self.cookies.name, "private-album", "private cookie contents"):
            self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return code, stdout.getvalue(), mocked

    def test_success_command_counts_and_cleanup(self):
        downloads = []

        def runner(command):
            self.assertEqual(command[:3], [sys.executable, "-m", "gallery_dl"])
            self.assertEqual(command[3:11], ["--cookies", str(self.cookies), "--download-archive",
                                           str(self.root / "archive"), "--sleep-request", "3-6", "-o", "videos=false"])
            self.assertEqual(command[11], "-d")
            tmp = Path(command[12])
            downloads.append(tmp)
            self.assertEqual(command[13:], ["--range", "1-2", "--filter",
                                           "date >= datetime(2025,2,3) or abort()", self.url])
            Image.new("RGB", (2, 3)).save(tmp / "secret title.png")
            return subprocess.CompletedProcess(command, 0, b"secret title", b"secret warning")

        code, output, _ = self.invoke(runner, ["--limit", "2", "--since", "2025-02-03", "--sleep", "3-6"])
        self.assertEqual(code, 0)
        self.assertEqual(output, "added 1, existing 0, unreadable 0\n")
        self.assertFalse(downloads[0].exists())

    def test_failure_output_is_private_and_temp_removed(self):
        downloads = []

        def runner(command):
            downloads.append(Path(command[command.index("-d") + 1]))
            return subprocess.CompletedProcess(command, 7, b"private filename", b"private stderr URL cookie")

        code, output, _ = self.invoke(runner)
        self.assertEqual(code, 1)
        self.assertEqual(output, "gallery-dl failed (exit 7)\n")
        self.assertFalse(downloads[0].exists())

    def test_bad_url_rejected(self):
        for url in ("http://facebook.com/photos", "https://facebook.com.evil/photos", "https://evil/facebook.com/"):
            self.args[1] = url
            code, output, runner = self.invoke(None)
            self.assertEqual((code, output), (1, "invalid url\n"))
            runner.assert_not_called()

    def test_missing_cookies_rejected(self):
        self.cookies.unlink()
        code, output, runner = self.invoke(None)
        self.assertEqual((code, output), (1, "missing cookies\n"))
        runner.assert_not_called()

    def test_parser_errors_do_not_echo_values(self):
        code, output, runner = self.invoke(None, ["--limit", "private-value"])
        self.assertEqual((code, output), (1, "invalid arguments\n"))
        runner.assert_not_called()

    def test_runner_captures_output(self):
        with patch.object(subprocess, "run") as run:
            ingest_facebook.run_gallery_dl(["command"])
        run.assert_called_once_with(["command"], capture_output=True)


if __name__ == "__main__":
    unittest.main()
