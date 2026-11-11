"""Incremental storage-backed photo scan entrypoint."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone

from PIL import Image, ImageOps

import assets
import build
import catalog as catalog_mod
import dedupe
import faces
import obfuscate
from remote import STATE_ALLOWLIST, QuotaDeferred, migrate, open_remote, supports_originals, usage

PIPE = Path(__file__).resolve().parent
GALLERY = PIPE.parent
DATA = GALLERY / "data"
SRC = Path(os.environ.get("RKMV_SOURCE", GALLERY.parent / "sorted"))
PEOPLE = Path(os.environ.get("RKMV_PEOPLE", GALLERY.parent / "hello" / "data"))
run_faces = faces


def now():
    return datetime.now(timezone.utc).isoformat()


def _safe_print(text):
    print(text, flush=True)


def _pipeline_output(verbose, function, *args, **kwargs):
    stream = io.StringIO()
    with redirect_stdout(stream):
        result = function(*args, **kwargs)
    if verbose:
        print(stream.getvalue(), end="")
    return result


def _status(remote, state, stage, step, steps, started, counts=None):
    old = remote.get_json("status/scan.json", {})
    if old.get("stage") != stage or old.get("state") != state:
        remote.put_json("status/scan.json", {"state": state, "stage": stage, "step": step, "steps": steps,
                         "startedAt": started, "updatedAt": now(), "queuedAt": old.get("queuedAt"),
                         "runUrl": os.environ.get("GITHUB_SERVER_URL", "") + "/" + os.environ.get("GITHUB_REPOSITORY", "") + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", ""),
                         "counts": counts or {"new": 0, "duplicates": 0, "faces": 0, "published": 0}})
        counts = counts or {}
        _safe_print(f"stage {stage}: new={counts.get('new', 0)} duplicates={counts.get('duplicates', 0)} "
                    f"faces={counts.get('faces', 0)} published={counts.get('published', 0)}")


def _pull(remote):
    PEOPLE.mkdir(parents=True, exist_ok=True)
    remote.pull_dir("people/", PEOPLE)
    DATA.mkdir(parents=True, exist_ok=True)
    keys = [key for key, _ in remote.ls("state/")]
    for key in keys:
        rel = key[len("state/"):]
        if rel == "fx.tar.gz" or any(Path(rel).match(pattern) or ("/**" in pattern and rel.startswith(pattern.split("/**")[0] + "/")) for pattern in STATE_ALLOWLIST):
            target = DATA / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = remote.get(key)
            if payload is not None:
                target.write_bytes(payload)
    remote.unpack_get("state/fx.tar.gz", DATA / "fx")


def _normalise(data, path):
    with Image.open(io.BytesIO(data)) as source:
        date = None
        try:
            date = source.getexif().get(36867)
            date = datetime.strptime(str(date), "%Y:%m:%d %H:%M:%S").date().isoformat() if date else None
        except ValueError:
            date = None
        rgb = ImageOps.exif_transpose(source).convert("RGB")
        clean = Image.new("RGB", rgb.size)
        clean.paste(rgb)
        clean.save(path, "JPEG", quality=92)
    return date


def _uploader(key):
    return key.split("/")[1] if key.count("/") >= 2 else ""


def _merge_corrections(remote):
    payload = remote.get("review/corrections.json")
    if payload is None:
        return False
    incoming = json.loads(payload.decode("utf-8"))
    path = DATA / "corrections.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for field, value in incoming.items():
        if isinstance(value, dict):
            current.setdefault(field, {}).update(value)
        elif isinstance(value, list):
            current[field] = list(dict.fromkeys(current.get(field, []) + value))
        else:
            current[field] = value
    path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    return True


def _apply_reviews(remote, catalog, statuses):
    changed = False
    kept = []
    deleted_images = []
    deleted_reviews = []
    marker_keys = {key for key, _ in remote.ls("review/")}
    for entry in catalog:
        key = f"review/{entry['id']}.json"
        marker = remote.get_json(key) if key in marker_keys else None
        if not marker or marker.get("action") not in ("approve", "reject"):
            kept.append(entry)
            continue
        changed = True
        uid = entry.get("uploader")
        item = {"key": entry.get("upload_key"), "photo": entry["id"], "at": now()}
        if marker["action"] == "approve":
            entry["pending"] = False
            item["state"] = "live"
            kept.append(entry)
        else:
            item["state"] = "rejected"
            (DATA / "fx" / entry["id"]).unlink(missing_ok=True)
            for cache in (DATA / "fx").glob(f"*/{entry['id']}.json"):
                cache.unlink(missing_ok=True)
            for sub in ("thumb", "web"):
                rel = f"img/{sub}/{entry['id']}.webp"
                image_key = os.environ.get("IMG_KEY")
                storage_rel = obfuscate.hashed(image_key.encode(), rel) if image_key else rel
                (GALLERY / rel).unlink(missing_ok=True)
                deleted_images.append(storage_rel)
            if entry.get("name"):
                (SRC / "uploads" / entry["name"]).unlink(missing_ok=True)
            deleted_images.append(f"originals/uploads/{entry['name']}")
        if uid:
            statuses.setdefault(uid, []).append(item)
        deleted_reviews.append(key)
    return kept, changed, deleted_images, deleted_reviews


def _write_user_status(remote, statuses):
    for uid, updates in statuses.items():
        key = f"status/uploads/{uid}.json"
        previous = remote.get_json(key, {"items": []})
        rows = {row.get("key") or row.get("photo"): row for row in previous.get("items", [])}
        for row in updates:
            rows[row.get("key") or row.get("photo")] = row
        recent = sorted(rows.values(), key=lambda row: row.get("at", ""))[-200:]
        remote.put_json(key, {"items": recent})


def _copy_missing_images(remote, cache, dest):
    cache, dest = Path(cache), Path(dest)
    cache.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    local_sizes = {p.relative_to(cache).as_posix(): p.stat().st_size for p in cache.rglob("*") if p.is_file()}
    copied = 0
    for key, size in remote.ls("img/"):
        rel = key[4:]
        target = cache / rel
        if local_sizes.get(rel) != size:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = remote.get(key)
            if data is not None:
                target.write_bytes(data)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copyfile(target, out)
            copied += 1
    return copied


def pages_config(env):
    """wrangler.toml for the real site, generated so PHOTOS always follows STORAGE_BACKEND.
    The repo's own wrangler.toml binds the public demo's KV and must never be used here."""
    safe = re.compile(r"^[A-Za-z0-9_./-]{1,128}$")
    values = {name: env.get(name, "") for name in ("PAGES_PROJECT", "FIREBASE_PROJECT_ID", "GITHUB_REPOSITORY", "STORAGE_BACKEND")}
    values["STORE"] = env.get("CF_KV_NAMESPACE_ID" if values["STORAGE_BACKEND"] == "kv" else "R2_BUCKET", "")
    bad = [name for name, value in values.items() if not safe.match(value)]
    if bad:
        raise SystemExit("missing or invalid setting: " + ", ".join(bad))
    store = values["STORE"]
    binding = (["[[kv_namespaces]]", 'binding = "PHOTOS"', f'id = "{store}"'] if values["STORAGE_BACKEND"] == "kv"
               else ["[[r2_buckets]]", 'binding = "PHOTOS"', f'bucket_name = "{store}"'])
    lines = [f'name = "{values["PAGES_PROJECT"]}"', 'compatibility_date = "2026-11-06"', 'pages_build_output_dir = "public"', "",
             *binding, "", "[vars]", f'FIREBASE_PROJECT_ID = "{values["FIREBASE_PROJECT_ID"]}"',
             f'GITHUB_REPO = "{values["GITHUB_REPOSITORY"]}"']
    return "\n".join(lines) + "\n"


def publish(remote, dest):
    """Lay out a Pages project: public/ (static site + obfuscated images), functions/, wrangler.toml.
    Data JSON is NOT copied: it names students and is served only by the login-checked Function."""
    dest = Path(dest)
    public = dest / "public"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GALLERY / "index.html", public / "index.html")
    for folder in ("css", "js"):
        shutil.copytree(GALLERY / folder, public / folder, dirs_exist_ok=True)
    shutil.copytree(GALLERY / "functions", dest / "functions", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("tests", "*.md"))
    (dest / "wrangler.toml").write_text(pages_config(os.environ), encoding="utf-8")
    cache = Path(os.environ.get("SMRITI_IMG_CACHE", Path.home() / ".cache" / "smriti-img"))
    return _copy_missing_images(remote, cache, public / "img")


def scan(args, remote=None):
    remote = remote or open_remote()
    verbose = getattr(args, "verbose", False)
    started = now()
    counts = {"new": 0, "duplicates": 0, "faces": 0, "published": 0}
    recorded, affected, statuses = [], set(), {}
    steps = 8
    stage = "pull"
    try:
        pending_snapshot = remote.ls("pending/")
        review_snapshot = remote.ls("review/")
        if (not pending_snapshot and not review_snapshot and not args.fetch_facebook and
                not args.recluster and not args.reembed and not args.eval):
            _status(remote, "done", "done", steps, steps, started, counts)
            _safe_print("scan complete: new=0 duplicates=0 faces=0 published=0")
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as file:
                    file.write("publish=false\n")
            return 0
        _status(remote, "running", stage, 1, steps, started, counts)
        _pull(remote)
        if args.eval:
            import eval_match
            _pipeline_output(verbose, eval_match.main, ["--clusters"])
            _status(remote, "done", "eval", 1, steps, started, counts)
            return 0
        if args.recluster:
            path = DATA / "auto_people.json"
            obj = json.loads(path.read_text()) if path.exists() else {"next": 1}
            obj["groups"] = {}
            path.write_text(json.dumps(obj), encoding="utf-8")
        stage = "ingest"
        _status(remote, "running", stage, 2, steps, started, counts)
        pending = pending_snapshot
        recorded = [key for key, _ in pending]
        upload_records = []
        uploads_dir = SRC / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dates = {}
        for key, _ in pending:
            data = remote.get(key)
            if data is None:
                continue
            digest = hashlib.sha256(data).hexdigest()
            filename = f"up-{digest[:16]}.jpg"
            try:
                date = _normalise(data, uploads_dir / filename)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                (uploads_dir / filename).unlink(missing_ok=True)
                uid = _uploader(key)
                affected.add(uid)
                statuses.setdefault(uid, []).append({"key": key, "state": "unreadable", "at": now()})
                continue
            dates[filename] = date
            uid = _uploader(key)
            affected.add(uid)
            upload_records.append((key, uid, filename, digest, date))
        if args.fetch_facebook:
            cookies = remote.get("secrets/fb-cookies.txt")
            url = remote.get("secrets/fb-url.txt")
            if cookies and url:
                cookie_path = PIPE / ".fb-cookies.txt"
                try:
                    cookie_path.write_bytes(cookies)
                    text_url = url.decode().strip()
                    print(f"::add-mask::{text_url}")
                    for line in cookies.decode(errors="ignore").splitlines():
                        parts = line.split("\t")
                        if len(parts) >= 7 and parts[6]:
                            print(f"::add-mask::{parts[6]}")
                    from ingest_facebook import main as facebook_main
                    before_fb = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.jpg")}
                    _pipeline_output(verbose, facebook_main, ["--url", text_url, "--cookies", str(cookie_path), "--dest", str(SRC), "--archive", str(DATA / "fb_archive.sqlite3"), "--limit", "300", "--sleep", "3-7"])
                    for path in sorted((SRC / "facebook").glob("*.jpg")):
                        rel = path.relative_to(SRC).as_posix()
                        if rel not in before_fb:
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                            shutil.copy2(path, uploads_dir / path.name)
                            upload_records.append((None, "", path.name, digest, catalog_mod.parse_date(path.name)))
                finally:
                    cookie_path.unlink(missing_ok=True)
        if args.reembed and supports_originals(remote):
            remote.pull_dir("originals/", SRC)   # every album, so archive photos are re-embedded too
        stage = "dedupe"
        _status(remote, "running", stage, 3, steps, started, counts)
        path = DATA / "catalog.json"
        catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        known = [{**e, "w": e.get("w", e.get("width", 0)), "h": e.get("h", e.get("height", 0))}
                 for e in catalog if e.get("sha") and e.get("ph")]
        added = []
        reused = []
        for key, uid, filename, digest, date in upload_records:
            info = dedupe.image_info(uploads_dir / filename)
            prior = next((entry for entry in catalog if entry.get("upload_key") == key), None) if key else None
            if prior:
                reused.append(prior)
                if uid:
                    state = "awaiting_approval" if prior.get("pending") else "live"
                    statuses.setdefault(uid, []).append({"key": key, "state": state, "photo": prior["id"], "at": now()})
                known.append({**info, "id": prior["id"]})
                continue
            match = dedupe.find_match(info, known)
            if match and match[0] == "duplicate":
                if uid:
                    statuses.setdefault(uid, []).append({"key": key, "state": "duplicate", "dupOf": match[1], "at": now()})
                counts["duplicates"] += 1
                continue
            extra = {"sha": info["sha"], "ph": info["ph"], "pending": True}
            if uid:
                extra.update(uploader=uid, upload_key=key)
            entry = catalog_mod.entry_for(uploads_dir / filename, f"uploads/{filename}", "uploads", extra)
            entry["date"] = date or entry["date"]
            added.append(entry)
            known.append({**info, "id": entry["id"]})
            if uid:
                statuses.setdefault(uid, []).append({"key": key, "state": "awaiting_approval", "photo": entry["id"], "at": now()})
        stage = "add"
        _status(remote, "running", stage, 4, steps, started, counts)
        catalog = catalog_mod.add_entries(catalog, added) if added else catalog
        counts["new"] = len(added)
        process_entries = added + reused
        if supports_originals(remote):
            # keep the normalised JPEG (EXIF stripped) the catalog points at, so a later reembed finds it
            remote.put_many([(f"originals/uploads/{e['name']}", (uploads_dir / e["name"]).read_bytes(), "image/jpeg")
                             for e in process_entries if (uploads_dir / e["name"]).exists()])
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        stage = "assets"
        _status(remote, "running", stage, 5, steps, started, counts)
        ids = [e["id"] for e in process_entries]
        errors = assets.process_ids(catalog, ids)
        if errors:
            raise RuntimeError("asset generation failed")
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        stage = "faces"
        _status(remote, "running", stage, 6, steps, started, counts)
        reembed = args.reembed and supports_originals(remote)
        if ids or reembed:
            _pipeline_output(verbose, run_faces.extract, reembed=reembed)
        if ids or args.recluster or reembed:
            _pipeline_output(verbose, run_faces.match)
        faces_map = json.loads((DATA / "faces.json").read_text()) if (DATA / "faces.json").exists() else {}
        counts["faces"] = sum(len(faces_map.get(pid, [])) for pid in ids)
        stage = "review"
        _status(remote, "running", stage, 7, steps, started, counts)
        corrections_merged = _merge_corrections(remote)
        if corrections_merged and (DATA / "faces_raw.json").exists():
            _pipeline_output(verbose, run_faces.match)
        catalog, changed, deleted_images, deleted_reviews = _apply_reviews(remote, catalog, statuses)
        counts["published"] = sum(1 for rows in statuses.values() for row in rows if row.get("state") == "live")
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        stage = "build"
        _status(remote, "running", stage, 8, steps, started, counts)
        _pipeline_output(verbose, build.main)
        img_written = False
        site_data = DATA   # data JSON to upload; replaced by the renamed copy when IMG_KEY is set
        key = os.environ.get("IMG_KEY")
        if key:
            key_file = PIPE / ".secrets" / "img-key.txt"
            key_file.parent.mkdir(parents=True, exist_ok=True)
            old_mask = os.umask(0o077)
            try:
                key_file.write_text(key, encoding="utf-8")
                temp = Path(tempfile.mkdtemp())
                obfuscate.build(GALLERY, temp, key.encode(), require_files=False)
                image_dir = temp / "img"
                if image_dir.exists():
                    img_written = remote.push_dir(image_dir, "img/", only_changed=True) > 0
                site_data = temp / "data"
            finally:
                key_file.unlink(missing_ok=True)
                os.umask(old_mask)
        else:
            img_written = remote.push_dir(GALLERY / "img", "img/", only_changed=True) > 0
        for name in ("photos", "people", "graph", "questions", "clusters", "pending"):
            target = site_data / f"{name}.json"
            if target.exists():
                remote.put(f"data/{name}.json", target.read_bytes(), "application/json")
        if site_data != DATA:
            shutil.rmtree(site_data.parent, ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for pattern in STATE_ALLOWLIST:
                for item in DATA.glob(pattern):
                    if item.is_file():
                        target = state / item.relative_to(DATA)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, target)
            remote.push_dir(state, "state/", only_changed=True)
        fx = DATA / "fx"
        if fx.exists():
            remote.pack_put("state/fx.tar.gz", fx)
        _write_user_status(remote, statuses)
        usage(remote)
        _status(remote, "done", "done", steps, steps, started, counts)
        output = os.environ.get("GITHUB_OUTPUT")
        publish = img_written or changed
        if output:
            with open(output, "a", encoding="utf-8") as file:
                file.write(f"publish={'true' if publish else 'false'}\n")
        completed = [key for key in recorded if any(row.get("key") == key for rows in statuses.values() for row in rows)]
        to_delete = deleted_images + deleted_reviews + completed
        if corrections_merged:
            to_delete.append("review/corrections.json")
        remote.delete(to_delete)
        _safe_print(f"scan complete: new={counts['new']} duplicates={counts['duplicates']} faces={counts['faces']} published={counts['published']}")
        return 0
    except QuotaDeferred:
        _status(remote, "done", "waiting for storage quota", 0, steps, started, counts)
        _safe_print("scan waiting for storage quota")
        return 0
    except Exception as error:
        _status(remote, "failed", stage, 0, steps, started, counts)
        # The exception type is safe to log; its message and traceback may hold file names, so only in verbose runs.
        _safe_print(f"scan failed at {stage}: {type(error).__name__}")
        if verbose:
            traceback.print_exc()
        return 1
    finally:
        if remote.budget:
            remote.budget.save()   # KV daily write budget, kept across runs


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-facebook", action="store_true")
    parser.add_argument("--recluster", action="store_true")
    parser.add_argument("--reembed", action="store_true")
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--migrate-from", choices=("kv", "r2"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--backfill-hashes")
    parser.add_argument("--publish-dir")
    args = parser.parse_args(argv)
    if args.backfill_hashes:
        return backfill_hashes(args.backfill_hashes)
    if args.publish_dir:
        publish(open_remote(), args.publish_dir)
        return 0
    if args.migrate_from:
        source_env = dict(os.environ, STORAGE_BACKEND=args.migrate_from)
        result = migrate(open_remote(source_env), open_remote())
        usage(open_remote())
        _safe_print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.reembed and os.environ.get("STORAGE_BACKEND") == "kv":
        _safe_print("reembed needs R2; continuing without it")
        args.reembed = False
    try:
        remote = open_remote()
    except SystemExit as missing:
        # The daily cron must not fail every day on a fork or before setup; a manual run still fails loudly.
        if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            _safe_print(f"scheduled scan skipped: {missing}")
            return 0
        raise
    return scan(args, remote)


def backfill_hashes(source):
    root = Path(source)
    catalog_path = DATA / "catalog.json"
    entries = json.loads(catalog_path.read_text(encoding="utf-8"))
    by_file = {entry["file"]: entry for entry in entries}
    corrected = json.loads((DATA / "corrections.json").read_text(encoding="utf-8")) if (DATA / "corrections.json").exists() else {}
    candidates = []
    for path in sorted(root.glob("*/*.jpg")):
        entry = by_file.get(f"{path.parent.name}/{path.name}")
        if entry:
            info = dedupe.image_info(path)
            entry.update(sha=info["sha"], ph=info["ph"])
            candidates.append({**entry, **info})
    confirmed = corrected.get("faces", {})
    def score(entry):
        faces = sum(1 for key in confirmed if key.startswith(entry["id"] + ":"))
        date = int((entry.get("date") or "9999-12-31").replace("-", ""))
        return faces, entry["width"] * entry["height"], -date
    duplicates = dedupe.group_duplicates(candidates, score)
    for entry in entries:
        if entry["id"] in duplicates:
            entry["dup_of"] = duplicates[entry["id"]]
    catalog_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    _safe_print(f"hashes={len(candidates)} duplicates={len(duplicates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
