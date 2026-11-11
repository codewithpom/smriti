"""Build a deployable copy of the gallery data with unguessable image names.

  python pipeline/obfuscate.py --src <dir with data/ and img/> --out <empty dir>

Writes <out>/data/*.json with every image path rewritten and <out>/img/<hash>.<ext> with only the
images those files reference. The hash is HMAC-SHA256(key, original relative path), so the original
id can't be turned into a URL without the key. The key lives in pipeline/.secrets/img-key.txt
(gitignored, created on first run) so names stay stable between deploys and browser caches keep working.
Nothing here prints file names or people.
"""
import argparse
import hashlib
import hmac
import json
import secrets
import shutil
import sys
from pathlib import Path

DATA_FILES = ("photos", "people", "graph", "questions", "clusters", "pending")
KEY_FILE = Path(__file__).resolve().parent / ".secrets" / "img-key.txt"


def load_key():
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return KEY_FILE.read_text(encoding="utf-8").strip().encode()


def hashed(key, rel):
    return "img/" + hmac.new(key, rel.encode(), hashlib.sha256).hexdigest()[:24] + Path(rel).suffix.lower()


def walk(obj, fn):
    if isinstance(obj, dict):
        return {k: walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, fn) for v in obj]
    return fn(obj) if isinstance(obj, str) else obj


def build(src, out, key, require_files=True):
    src, out = Path(src), Path(out)
    mapping, missing = {}, set()

    def remap(value):
        if not value.startswith("img/"):
            return value
        if value not in mapping:
            exists = (src / value).is_file()
            if exists or not require_files:
                mapping[value] = hashed(key, value)
            else:
                missing.add(value)
                return value
        return mapping[value]

    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        p = src / "data" / f"{name}.json"
        if not p.exists():
            continue
        data = walk(json.loads(p.read_text(encoding="utf-8")), remap)
        (out / "data" / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for old, new in mapping.items():
        if (src / old).is_file():
            shutil.copyfile(src / old, out / new)
    return len(mapping), len(missing)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if any(Path(a.out).glob("*")):
        sys.exit("--out must be empty")
    n, missing = build(a.src, a.out, load_key())
    print(f"images renamed: {n}, referenced but missing on disk: {missing}")
