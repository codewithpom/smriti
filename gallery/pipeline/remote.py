"""Small storage adapters used by the photo pipeline and laptop tools."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

CHUNK_SIZE = 24 * 1024 * 1024
_MISSING = object()


class QuotaDeferred(Exception):
    """The requested KV operation would exceed its daily budget."""


class Remote:
    backend = "remote"
    originals = True

    def __init__(self):
        self.stats = dict(reads=0, writes=0, deletes=0, lists=0, bytes_out=0, bytes_in=0)
        self.budget = None

    def get_json(self, key, default=None):
        data = self.get(key)
        return default if data is None else json.loads(data.decode("utf-8"))

    def put_json(self, key, obj):
        self.put(key, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(), "application/json")

    def pull_dir(self, prefix, dest):
        dest = Path(dest)
        count = 0
        for key, _ in self.ls(prefix):
            rel = key[len(prefix.rstrip("/")) + 1:] if prefix.rstrip("/") else key
            target = dest.joinpath(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.get(key) or b"")
            count += 1
        return count

    def push_dir(self, src, prefix, only_changed=True):
        src, prefix = Path(src), prefix.strip("/")
        manifest_key = f"state/manifest/{prefix.replace('/', '_')}.json"
        old = self.get_json(manifest_key, {}) if only_changed else {}
        new, items = {}, []
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(src).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha1(data).hexdigest()
            new[rel] = digest
            if old.get(rel) != digest:
                items.append((f"{prefix}/{rel}" if prefix else rel, data, _content_type(path)))
        self.put_many(items)
        if only_changed:
            self.put_json(manifest_key, new)
        return len(items)

    def put_many(self, items):
        for key, data, content_type in items:
            self.put(key, data, content_type)

    def pack_put(self, key, src_dir):
        with tempfile.TemporaryFile() as file:
            with tarfile.open(fileobj=file, mode="w:gz") as archive:
                for path in sorted(Path(src_dir).rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(src_dir).as_posix(), recursive=False)
            file.seek(0)
            self.put(key, file.read(), "application/gzip")
        return True

    def unpack_get(self, key, dest_dir):
        data = self.get(key)
        if data is None:
            return False
        dest = Path(dest_dir).resolve()
        with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                target = (dest / member.name).resolve()
                if target != dest and dest not in target.parents:
                    raise ValueError("unsafe archive path")
                if member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source:
                        target.write_bytes(source.read())
        return True


def _content_type(path):
    suffix = Path(path).suffix.lower()
    return {".json": "application/json", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix)


class LocalRemote(Remote):
    backend = "local"

    def __init__(self, root):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        p = (self.root / Path(*PurePosixPath(key).parts)).resolve()
        if p != self.root.resolve() and self.root.resolve() not in p.parents:
            raise ValueError("invalid key")
        return p

    def ls(self, prefix=""):
        self.stats["lists"] += 1
        base = prefix.rstrip("/")
        out = []
        for p in self.root.rglob("*"):
            if p.is_file():
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix) and (not base or key == base or key.startswith(base + "/")):
                    out.append((key, p.stat().st_size))
        return sorted(out)

    def get(self, key):
        p = self._path(key)
        self.stats["reads"] += 1
        if not p.is_file():
            return None
        data = p.read_bytes()
        self.stats["bytes_out"] += len(data)
        return data

    def put(self, key, data, content_type=None):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.stats["writes"] += 1
        self.stats["bytes_in"] += len(data)

    def delete(self, keys):
        for key in keys:
            self._path(key).unlink(missing_ok=True)
        self.stats["deletes"] += bool(keys)


class R2Remote(Remote):
    backend = "r2"

    def __init__(self, bucket, run=subprocess.run):
        super().__init__()
        self.bucket, self.run = bucket, run

    def _call(self, args, input=None):
        result = self.run(["rclone", *args, "--log-level", "ERROR"], input=input, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError("rclone operation failed")
        return result.stdout or b""

    def _remote(self, key=""):
        return f"r2:{self.bucket}" + (f"/{key.lstrip('/')}" if key else "")

    def ls(self, prefix=""):
        self.stats["lists"] += 1
        try:
            rows = json.loads(self._call(["lsjson", "-R", "--files-only", self._remote(prefix)]).decode() or "[]")
        except RuntimeError:
            return []   # rclone errors when the prefix does not exist yet
        base = prefix.rstrip("/")
        return sorted(((base + "/" if base else "") + row["Path"], row["Size"]) for row in rows)

    def get(self, key):
        self.stats["reads"] += 1
        try:
            data = self._call(["cat", self._remote(key)])
        except RuntimeError:
            if not self.ls(key):
                return None   # missing key, same contract as KV and local
            raise
        self.stats["bytes_out"] += len(data)
        return data

    def put(self, key, data, content_type=None):
        self._call(["rcat", self._remote(key)], input=data)
        self.stats["writes"] += 1
        self.stats["bytes_in"] += len(data)

    def delete(self, keys):
        if keys:
            self._call(["delete", "--files-from-raw", "-", self._remote()], input="\n".join(keys).encode())
            self.stats["deletes"] += 1

    def pull_dir(self, prefix, dest):
        Path(dest).mkdir(parents=True, exist_ok=True)
        self._call(["copy", self._remote(prefix), str(dest)])
        return len(self.ls(prefix))

    def push_dir(self, src, prefix, only_changed=True):
        self._call(["copy", str(src), self._remote(prefix)])
        return sum(1 for p in Path(src).rglob("*") if p.is_file())


class FirebaseStorageRemote(Remote):
    """Google Cloud Storage JSON API adapter for the Firebase Storage bucket."""

    backend = "firebase"
    originals = True
    scope = "https://www.googleapis.com/auth/devstorage.read_write"
    api_root = "https://storage.googleapis.com/storage/v1"
    upload_root = "https://storage.googleapis.com/upload/storage/v1"

    def __init__(self, bucket, credentials_file=None, session=None):
        super().__init__()
        self.bucket = bucket
        if session is None:
            if not credentials_file:
                raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is required")
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=[self.scope])
            session = AuthorizedSession(credentials)
        self.session = session
        self.bucket_path = f"/b/{quote(bucket, safe='')}/o"

    def _request(self, method, url, *, allow_missing=False, **kwargs):
        response = self.session.request(method, url, timeout=120, **kwargs)
        status = response.status_code
        if status == 404 and allow_missing:
            return None
        if status >= 400:
            raise RuntimeError(f"Firebase Storage request failed ({status})")
        return response

    def _object_url(self, key, *, download=False):
        root = "https://storage.googleapis.com/download/storage/v1" if download else self.api_root
        return f"{root}{self.bucket_path}/{quote(key, safe='')}"

    def ls(self, prefix=""):
        out, page_token = [], None
        while True:
            params = {"prefix": prefix, "pageSize": 1000, "fields": "items(name,size),nextPageToken"}
            if page_token:
                params["pageToken"] = page_token
            response = self._request("GET", f"{self.api_root}{self.bucket_path}", params=params)
            payload = response.json()
            for item in payload.get("items", []):
                out.append((item["name"], int(item.get("size", 0))))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        self.stats["lists"] += 1
        return out

    def get(self, key):
        response = self._request("GET", self._object_url(key, download=True),
                                 params={"alt": "media"}, allow_missing=True)
        self.stats["reads"] += 1
        if response is None:
            return None
        payload = response.content
        self.stats["bytes_out"] += len(payload)
        return payload

    def put(self, key, data, content_type=None):
        payload = bytes(data)
        self._upload(key, payload, len(payload), content_type)
        self.stats["writes"] += 1
        self.stats["bytes_in"] += len(payload)

    def _upload(self, key, source, size, content_type=None):
        url = f"{self.upload_root}{self.bucket_path}"
        params = {"uploadType": "media", "name": key}
        self._request("POST", url, params=params, data=source,
                      headers={"Content-Type": content_type or "application/octet-stream",
                               "Content-Length": str(size)})

    def pull_dir(self, prefix, dest):
        dest = Path(dest)
        root = prefix.rstrip("/")
        count = 0
        list_prefix = f"{root}/" if root else ""
        for key, _ in self.ls(list_prefix):
            rel = key[len(root) + 1:] if root else key
            if not rel or rel.endswith("/"):
                continue
            safe = PurePosixPath(rel)
            if safe.is_absolute() or ".." in safe.parts or "\\" in rel or ":" in rel:
                raise ValueError("invalid object path")
            target = dest.joinpath(*safe.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            response = self._request("GET", self._object_url(key, download=True),
                                     params={"alt": "media"}, stream=True, allow_missing=True)
            if response is None:
                continue
            try:
                with target.open("wb") as output:
                    if hasattr(response, "iter_content"):
                        for block in response.iter_content(chunk_size=1024 * 1024):
                            if block:
                                output.write(block)
                                self.stats["bytes_out"] += len(block)
                    else:
                        output.write(response.content)
                        self.stats["bytes_out"] += len(response.content)
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
            self.stats["reads"] += 1
            count += 1
        return count

    def push_dir(self, src, prefix, only_changed=True):
        src, prefix = Path(src), prefix.strip("/")
        manifest_key = f"state/manifest/{prefix.replace('/', '_')}.json"
        old = self.get_json(manifest_key, {}) if only_changed else {}
        new, copied = {}, 0
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            digest = hashlib.sha1()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            rel = path.relative_to(src).as_posix()
            hexdigest = digest.hexdigest()
            new[rel] = hexdigest
            if old.get(rel) == hexdigest:
                continue
            key = f"{prefix}/{rel}" if prefix else rel
            size = path.stat().st_size
            with path.open("rb") as source:
                self._upload(key, source, size, _content_type(path))
            self.stats["writes"] += 1
            self.stats["bytes_in"] += size
            copied += 1
        if only_changed:
            self.put_json(manifest_key, new)
        return copied

    def pack_put(self, key, src_dir):
        with tempfile.TemporaryFile() as file:
            with tarfile.open(fileobj=file, mode="w:gz") as archive:
                for path in sorted(Path(src_dir).rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(src_dir).as_posix(), recursive=False)
            size = file.tell()
            file.seek(0)
            self._upload(key, file, size, "application/gzip")
        self.stats["writes"] += 1
        self.stats["bytes_in"] += size
        return True

    def delete(self, keys):
        deleted = 0
        for key in keys:
            response = self._request("DELETE", self._object_url(key), allow_missing=True)
            if response is not None:
                deleted += 1
        self.stats["deletes"] += deleted


class KVRemote(Remote):
    backend, originals = "kv", False

    def __init__(self, account_id, namespace_id, token, opener=urlopen, budget=None, sleep=time.sleep):
        super().__init__()
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{quote(account_id, safe='')}/storage/kv/namespaces/{quote(namespace_id, safe='')}"
        self.token, self.opener, self.budget, self.sleep = token, opener, budget, sleep

    def _request(self, method, path, data=None, operation=None, exempt=False, units=1):
        request = Request(self.base + path, data=data, method=method,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"} if data is not None else {"Authorization": f"Bearer {self.token}"})
        if operation and self.budget and not exempt:
            self.budget.consume(operation, units)   # KV bills every key in a bulk call, so count keys, once, before sending
        for attempt in range(4):
            try:
                response = self.opener(request)
                body = response.read()
                status = getattr(response, "status", 200)
                if status >= 500 or status == 429:
                    if attempt == 3:
                        raise RuntimeError("Cloudflare request failed")
                    self.sleep(2 ** attempt)
                    continue
                return body
            except HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < 3:
                    self.sleep(2 ** attempt)
                    continue
                raise

    def ls(self, prefix=""):
        out, cursor = [], ""
        while True:
            query = urlencode({"prefix": prefix, "cursor": cursor, "limit": 1000})
            body = json.loads(self._request("GET", "/keys?" + query, operation="lists"))
            self.stats["lists"] += 1
            for row in body.get("result", []):
                key = row["name"]
                if ".~part" not in key:
                    out.append((key, (row.get("metadata") or {}).get("size", 0)))
            cursor = body.get("result_info", {}).get("cursor") or body.get("cursor") or ""
            if not body.get("result_info", {}).get("cursor") and not body.get("result_info", {}).get("count", 0) >= 1000:
                break
            if not cursor:
                break
        return out

    def get(self, key):
        try:
            data = self._request("GET", "/values/" + quote(key, safe=""))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        self.stats["reads"] += 1
        self.stats["bytes_out"] += len(data)
        try:
            marker = json.loads(data)
            if isinstance(marker, dict) and marker.get("chunked"):
                chunks = b"".join(self.get(f"{key}.~part{i}") or b"" for i in range(marker["chunked"]))
                if len(chunks) != marker["size"] or hashlib.sha1(chunks).hexdigest() != marker["sha1"]:
                    raise ValueError("corrupt chunked value")
                return chunks
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return data

    def put(self, key, data, content_type=None):
        if self.budget:
            self.budget.check("writes")
        if len(data) > CHUNK_SIZE:
            parts = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
            existing = self.get(key)
            old_parts = 0
            if existing is not None:
                try:
                    marker = json.loads(existing)
                    old_parts = marker.get("chunked", 0) if isinstance(marker, dict) else 0
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            self.put_many([(f"{key}.~part{i}", part, content_type) for i, part in enumerate(parts)])
            marker = json.dumps({"chunked": len(parts), "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}).encode()
            self.put_many([(key, marker, None, {"chunked": True, "size": len(data)})])
            if old_parts > len(parts):
                self.delete([f"{key}.~part{i}" for i in range(len(parts), old_parts)])
            return
        existing = self.get(key)
        old_parts = 0
        if existing is not None:
            try:
                marker = json.loads(existing)
                old_parts = marker.get("chunked", 0) if isinstance(marker, dict) else 0
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        self.put_many([(key, data, content_type)])
        if old_parts:
            self.delete([f"{key}.~part{i}" for i in range(old_parts)])

    def put_many(self, items):
        norm = []
        for item in items:
            key, data, content_type, *extra = item
            if len(data) > CHUNK_SIZE:
                self.put(key, data, content_type)   # over the 25 MiB value limit: chunked path
                continue
            metadata = extra[0] if extra else {"httpMetadata": {"contentType": content_type} if content_type else None, "size": len(data)}
            norm.append({"key": key, "value": base64.b64encode(data).decode("ascii"), "base64": True, "metadata": metadata})
        batch = []
        size = 2
        for row in norm:
            encoded = json.dumps(row, separators=(",", ":")).encode()
            if batch and (len(batch) >= 100 or size + len(encoded) + 1 > 90 * 1024 * 1024):
                self._bulk_put(batch)
                batch, size = [], 2
            batch.append(row)
            size += len(encoded) + 1
        if batch:
            self._bulk_put(batch)

    def _bulk_put(self, batch):
        payload = json.dumps(batch, separators=(",", ":")).encode()
        self._request("PUT", "/bulk", payload, "writes", units=len(batch))
        self.stats["writes"] += len(batch)
        self.stats["bytes_in"] += sum(len(base64.b64decode(row["value"])) for row in batch)

    def delete(self, keys):
        for start in range(0, len(keys), 1000):
            batch = keys[start:start + 1000]
            self._request("POST", "/bulk/delete", json.dumps(batch).encode(), "deletes", units=len(batch))
            self.stats["deletes"] += len(batch)


class Budget:
    def __init__(self, remote_for_state, limits=None, today=None):
        self.remote = remote_for_state
        self.limits = {name: int((limits or {}).get(name, 700)) for name in ("writes", "deletes", "lists")}
        self.today = today or (lambda: datetime.now(timezone.utc).date().isoformat())
        saved = remote_for_state.get_json("state/kv_budget.json", {})
        self.state = saved if saved.get("date") == self.today() else {"date": self.today(), "writes": 0, "deletes": 0, "lists": 0}

    def consume(self, operation, units=1):
        self.check(operation, units)
        self.state[operation] += units

    def check(self, operation, units=1):
        if self.state[operation] + units > self.limits[operation]:
            raise QuotaDeferred(operation)

    def save(self):
        data = json.dumps(self.state, separators=(",", ":")).encode()
        # The budget record is bookkeeping required to resume safely, so its write bypasses the exhausted user budget.
        if isinstance(self.remote, KVRemote):
            self.remote._request("PUT", "/bulk", json.dumps([{"key": "state/kv_budget.json", "value": base64.b64encode(data).decode(), "base64": True, "metadata": {"httpMetadata": {"contentType": "application/json"}, "size": len(data)}}]).encode(), exempt=True)
        else:
            self.remote.put("state/kv_budget.json", data, "application/json")


def supports_originals(remote):
    return remote.backend != "kv"


def open_remote(env=os.environ):
    backend = env.get("STORAGE_BACKEND", "")
    if backend == "kv":
        names = ("CLOUDFLARE_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CLOUDFLARE_API_TOKEN")
        values = [env.get(name) for name in names]
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            raise SystemExit("missing setting: " + ", ".join(missing))
        remote = KVRemote(*values)
        limits = {name: int(env.get("KV_DAILY_BUDGET", "700")) for name in ("writes", "deletes", "lists")}
        remote.budget = Budget(remote, limits)
        return remote
    if backend == "r2":
        if not env.get("R2_BUCKET"):
            raise SystemExit("missing setting: R2_BUCKET")
        return R2Remote(env["R2_BUCKET"])
    if backend == "firebase":
        names = ("FIREBASE_STORAGE_BUCKET", "GOOGLE_APPLICATION_CREDENTIALS")
        missing = [name for name in names if not env.get(name)]
        if missing:
            raise SystemExit("missing setting: " + ", ".join(missing))
        credentials_file = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
        if not credentials_file.is_file():
            raise SystemExit("missing setting: GOOGLE_APPLICATION_CREDENTIALS file")
        return FirebaseStorageRemote(env["FIREBASE_STORAGE_BUCKET"], credentials_file)
    if backend == "local":
        if not env.get("STORAGE_LOCAL_DIR"):
            raise SystemExit("missing setting: STORAGE_LOCAL_DIR")
        return LocalRemote(env["STORAGE_LOCAL_DIR"])
    raise SystemExit("missing or invalid setting: STORAGE_BACKEND")


def migrate(src, dst, prefixes=("pending/", "img/", "data/", "state/", "status/", "review/", "secrets/", "people/", "originals/")):
    copied = skipped = 0
    try:
        dst_keys = dict(dst.ls(""))
        for prefix in prefixes:
            if prefix == "originals/" and not supports_originals(dst):
                skipped += len(src.ls(prefix))
                continue
            for key, size in src.ls(prefix):
                if key in dst_keys and dst_keys[key] == size:
                    continue
                data = src.get(key)
                if data is not None:
                    dst.put(key, data)
                    copied += 1
    except QuotaDeferred:
        return {"copied": copied, "skipped": skipped, "deferred": True, "verified": False}
    verified = True
    for prefix in prefixes:
        if prefix == "originals/" and not supports_originals(dst):
            continue
        target = dict(dst.ls(prefix))
        for key, size in src.ls(prefix):
            if target.get(key) != size:
                verified = False
                break
    return {"copied": copied, "skipped": skipped, "deferred": False, "verified": verified}


def usage(remote):
    entries = remote.ls("")
    grouped = {}
    for key, size in entries:
        prefix = key.split("/", 1)[0] + "/" if "/" in key else ""
        row = grouped.setdefault(prefix, {"keys": 0, "bytes": 0})
        row["keys"] += 1
        row["bytes"] += size
    result = {"backend": remote.backend, "keys": len(entries), "bytes": sum(size for _, size in entries), "byPrefix": grouped,
              "at": datetime.now(timezone.utc).isoformat()}
    remote.put_json("status/storage.json", result)
    return result


STATE_ALLOWLIST = ("corrections.json", "catalog.json", "ref_emb.npy", "ref_ids.json", "ref_src.json", "ref_failed.json", "extra_*.json", "manual_aliases.json", "people_meta.json", "auto_people.json", "aliases.json", "tags_shard_*.json", "face_emb.npy", "faces_raw.json", "faces.json", "hashes.json", "recovery_*.json", "fb_archive.sqlite3", "extra_refs/**/*")   # "dir/**" alone matches only folders in pathlib
SITE_JSON = ("photos", "people", "graph", "questions", "clusters")


def seed(remote, data, site=None, people=None, originals=None):
    """data: gallery/data (pipeline state). site: obfuscate.py output (renamed data/*.json + img/),
    so the served JSON and the stored images agree. Without site, the plain gallery/data JSON is used."""
    data = Path(data)
    site = Path(site) if site else None
    count = 0
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        for pattern in STATE_ALLOWLIST:
            for src in data.glob(pattern):
                if src.is_file():
                    target = state / src.relative_to(data)   # keep subfolders such as extra_refs/
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(src.read_bytes())
        count += remote.push_dir(state, "state/")
        fx = data / "fx"
        if fx.is_dir():
            remote.pack_put("state/fx.tar.gz", fx)
            count += 1
    for name in SITE_JSON:
        path = (site / "data" if site else data) / f"{name}.json"
        if path.is_file():
            remote.put(f"data/{name}.json", path.read_bytes(), "application/json")
            count += 1
    for directory, prefix in ((site / "img" if site else None, "img/"), (people, "people/")):
        if directory:
            count += remote.push_dir(directory, prefix)   # writes the manifest, so the first CI run uploads only new files
    if originals and supports_originals(remote):
        count += remote.push_dir(originals, "originals/")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("seed")
    p.add_argument("--data", required=True); p.add_argument("--site"); p.add_argument("--people"); p.add_argument("--originals")
    p = sub.add_parser("migrate")
    p.add_argument("--from", dest="source", choices=("kv", "r2", "local"), required=True); p.add_argument("--to", dest="target", choices=("kv", "r2", "local"), required=True)
    p.add_argument("--from-local-dir"); p.add_argument("--to-local-dir")
    sub.add_parser("usage")
    args = parser.parse_args(argv)
    remote = None
    try:
        if args.command == "seed":
            remote = open_remote()
            result = {"copied": seed(remote, args.data, args.site, args.people, args.originals)}
        elif args.command == "usage":
            remote = open_remote()
            result = usage(remote)
        else:
            env = dict(os.environ)
            env["STORAGE_BACKEND"] = args.source
            if args.from_local_dir: env["STORAGE_LOCAL_DIR"] = args.from_local_dir
            src = open_remote(env)
            env = dict(os.environ); env["STORAGE_BACKEND"] = args.target
            if args.to_local_dir: env["STORAGE_LOCAL_DIR"] = args.to_local_dir
            remote = open_remote(env)
            result = migrate(src, remote)
        print(json.dumps({k: v for k, v in result.items() if k not in ("byPrefix", "at")}, separators=(",", ":")))
        return 0
    except QuotaDeferred:
        print('{"deferred":true}')
        return 0
    finally:
        if remote is not None and getattr(remote, "budget", None):
            remote.budget.save()


if __name__ == "__main__":
    sys.exit(main())
