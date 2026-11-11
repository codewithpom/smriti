"""Download Facebook images and import metadata-free JPEGs with private logs."""
import argparse
from datetime import date
import hashlib
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import warnings

from PIL import Image, ImageOps


def import_images(src_dir, dest_dir, album="facebook"):
    """Return (added, existing, unreadable) for supported image extensions."""
    dest = Path(dest_dir) / album
    dest.mkdir(parents=True, exist_ok=True)
    added = existing = bad = 0
    for path in Path(src_dir).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        try:
            raw = path.read_bytes()
            target = dest / f"fb-{hashlib.sha1(raw).hexdigest()[:12]}.jpg"
            if target.exists():
                existing += 1
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with Image.open(io.BytesIO(raw)) as source:
                    rgb = ImageOps.exif_transpose(source).convert("RGB")
                    # A new image carries none of the source's EXIF or other info.
                    clean = Image.new("RGB", rgb.size)
                    clean.paste(rgb)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            bad += 1
            continue
        # Exclusive creation preserves existing images even during concurrent imports.
        try:
            with target.open("xb") as output:
                try:
                    clean.save(output, format="JPEG", quality=92)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
        except FileExistsError:
            existing += 1
        else:
            added += 1
    return added, existing, bad


def run_gallery_dl(args):
    """Module-level runner: replace this callable in offline tests."""
    return subprocess.run(args, capture_output=True)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes user-supplied values.
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(prog="ingest_facebook")
    for name in ("url", "cookies", "dest", "archive"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--since")
    parser.add_argument("--sleep", default="2-5")
    try:
        args = parser.parse_args(argv)
        if not re.match(r"^https://(www\.)?facebook\.com/", args.url):
            print("invalid url")
            return 1
        if not Path(args.cookies).is_file():
            print("missing cookies")
            return 1
        if args.limit < 0:
            raise ValueError
        since = None
        if args.since:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
                raise ValueError
            since = date.fromisoformat(args.since)
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, "-m", "gallery_dl", "--cookies", args.cookies,
                       "--download-archive", args.archive, "--sleep-request", args.sleep,
                       "-o", "videos=false", "-d", tmp]
            if args.limit:
                command += ["--range", f"1-{args.limit}"]
            if since:
                command += ["--filter", f"date >= datetime({since.year},{since.month},{since.day}) or abort()"]
            command.append(args.url)
            result = run_gallery_dl(command)
            if result.returncode:
                print(f"gallery-dl failed (exit {int(result.returncode)})")
                return 1
            added, existing, bad = import_images(tmp, args.dest)
        print(f"added {added}, existing {existing}, unreadable {bad}")
        return 0
    except ValueError:
        print("invalid arguments")
        return 1
    except Exception:
        # Exception messages can contain paths, URLs, or downloader details.
        print("failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
