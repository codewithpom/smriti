"""End-to-end check of ci_scan.py with real InsightFace on the fabricated demo faces.

  python -I gallery/demo/e2e_scan.py      (needs gallery/pipeline/requirements-scan.txt; downloads buffalo_l once)


Copies the code to a temp tree, seeds a LocalRemote with demo portraits + uploads (incl. duplicates),
runs the scan, approves half, runs again, publishes, and asserts the outcomes. Prints counts only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "gallery" / "demo"


def run(env, *args):
    out = subprocess.run([sys.executable, str(env["CODE"] / "gallery" / "pipeline" / "ci_scan.py"), *args],
                         env=env["ENV"], capture_output=True, text=True, timeout=3600)
    print(out.stdout.strip())
    if out.returncode:
        print(out.stderr[-3000:])
        raise SystemExit(f"ci_scan exited {out.returncode}")
    return out.stdout


def main():
    tmp = Path(tempfile.mkdtemp(prefix="smriti-e2e-"))
    code = tmp / "code"
    for part in ("pipeline", "functions", "js", "css"):
        shutil.copytree(REPO / "gallery" / part, code / "gallery" / part, ignore=shutil.ignore_patterns("__pycache__", ".secrets", "out", ".models"))
    shutil.copy2(REPO / "gallery" / "index.html", code / "gallery" / "index.html")
    store = tmp / "remote"
    people = json.loads((DEMO / "data" / "people.json").read_text(encoding="utf-8"))
    ident = {"students": [{"reg_no": p["reg_no"], "name": p["name"], "section": p.get("section")} for p in people if p["kind"] == "student"],
             "staff": []}
    (store / "people" / "reference_faces" / "students").mkdir(parents=True)
    (store / "people" / "identities.json").write_text(json.dumps(ident))
    for p in people:
        src = DEMO / "avatars-src" / f"{p['id']}.jpg"
        if src.exists() and p["kind"] == "student":
            shutil.copy2(src, store / "people" / "reference_faces" / "students" / f"{p['reg_no']}.jpg")
    photos = sorted((DEMO / "img" / "web").glob("*.webp"))[:12]
    uploads = []
    for n, path in enumerate(photos):
        uid = f"user{n % 3}"
        key = f"pending/{uid}/{1700000000000 + n}-00000000-0000-0000-0000-{n:012d}.jpg"
        target = store / key
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.open(path).convert("RGB").save(target, "JPEG", quality=92)
        uploads.append(key)
    # exact copy and a resized re-encode of the first photo
    dup = store / "pending" / "user0" / "1700000009998-dup.jpg"
    shutil.copy2(store / uploads[0], dup)
    small = store / "pending" / "user1" / "1700000009999-small.jpg"
    im = Image.open(store / uploads[0]); im.resize((im.width // 2, im.height // 2)).save(small, "JPEG", quality=60)

    env = dict(os.environ, STORAGE_BACKEND="local", STORAGE_LOCAL_DIR=str(store), GITHUB_OUTPUT=str(tmp / "out1"),
               RKMV_SOURCE=str(tmp / "sorted"), RKMV_PEOPLE=str(tmp / "people"), IMG_KEY="e2e" * 22)
    ctx = {"CODE": code, "ENV": env}
    log1 = run(ctx)
    status = lambda uid: {i["key"]: i for i in json.loads((store / "status" / "uploads" / f"{uid}.json").read_text())["items"]}
    states = [i["state"] for uid in ("user0", "user1", "user2") for i in status(uid).values()]
    pending = json.loads((store / "data" / "pending.json").read_text())["items"]
    faces = json.loads((code / "gallery" / "data" / "faces.json").read_text())
    stats = {}
    for rows in faces.values():
        for f in rows:
            stats[f["status"]] = stats.get(f["status"], 0) + 1
    clusters = {f["cluster"] for rows in faces.values() for f in rows if f["cluster"] is not None}
    print("run1 states:", {s: states.count(s) for s in set(states)}, "pending items:", len(pending), "face statuses:", stats, "clusters:", len(clusters))
    assert states.count("duplicate") == 2, "both duplicates caught"
    assert states.count("awaiting_approval") == 12 and len(pending) == 12
    assert sum(stats.values()) > 0 and stats.get("high", 0) > 0, "real faces detected and matched to portraits"
    assert not list((store / "pending").rglob("*.jpg")), "processed raw uploads deleted"
    assert "publish=true" in (tmp / "out1").read_text()
    assert all("/thumb/" not in i["thumb"] for i in pending), "renamed image paths"
    assert json.loads((store / "status" / "scan.json").read_text())["state"] == "done"
    for secret in ("user0", "user1", "pending/", "Demo Student"):
        assert secret not in log1, f"log leaked {secret!r}"

    for item in pending[:6]:
        (store / "review" / f"{item['photo']}.json").parent.mkdir(exist_ok=True)
        (store / "review" / f"{item['photo']}.json").write_text(json.dumps({"action": "approve", "by": "admin", "at": "now"}))
    (store / "review" / f"{pending[6]['photo']}.json").write_text(json.dumps({"action": "reject", "by": "admin", "at": "now"}))
    env["GITHUB_OUTPUT"] = str(tmp / "out2")
    run(ctx)
    live = json.loads((store / "data" / "photos.json").read_text())
    pending2 = json.loads((store / "data" / "pending.json").read_text())["items"]
    named = sum(1 for p in live for _ in p["people"])
    print("run2 published:", len(live), "still pending:", len(pending2), "named people tags:", named)
    assert len(live) == 6 and len(pending2) == 5
    assert not list((store / "review").glob("*.json"))
    env["GITHUB_OUTPUT"] = str(tmp / "out3")
    run(ctx)   # nothing pending: fast no-op
    assert "publish=false" in (tmp / "out3").read_text()

    site = tmp / "site"
    env.update(PAGES_PROJECT="mb27ndp", FIREBASE_PROJECT_ID="demo-project", GITHUB_REPOSITORY="o/r", STORAGE_BACKEND="local",
               SMRITI_IMG_CACHE=str(tmp / "imgcache"))
    env["STORAGE_BACKEND"] = "local"
    out = subprocess.run([sys.executable, "-c", (
        "import sys,os; sys.path.insert(0, r'%s'); os.environ['STORAGE_BACKEND']='local'; import ci_scan, remote;"
        "os.environ['STORAGE_BACKEND']='kv'; os.environ['CF_KV_NAMESPACE_ID']='ns'; "
        "print(ci_scan.publish(remote.LocalRemote(r'%s'), r'%s'))") % (code / "gallery" / "pipeline", store, site)],
        env=env, capture_output=True, text=True)
    print("publish images:", out.stdout.strip(), out.stderr[-500:])
    assert not (site / "public" / "data").exists() and (site / "functions").exists() and (site / "wrangler.toml").exists()
    served = {p.name for p in (site / "public" / "img").iterdir()}
    refs = {p["thumb"].split("/", 1)[1] for p in live}
    assert refs <= served, "every published thumbnail is deployed"
    truth = {Path(p["web"]).name: set(p["people"]) for p in json.loads((DEMO / "data" / "photos.json").read_text(encoding="utf-8"))}
    catalog = json.loads((code / "gallery" / "data" / "catalog.json").read_text())
    faces = json.loads((code / "gallery" / "data" / "faces.json").read_text())
    right = wrong = missed = 0
    for entry in catalog:
        if entry.get("upload_key") and "-0000-" in entry["upload_key"]:
            want = truth[photos[int(entry["upload_key"].rsplit("-", 1)[1].split(".")[0])].name]
            got = {f["person"] for f in faces.get(entry["id"], []) if f["status"] in ("high", "confirmed")}
            right, wrong, missed = right + len(got & want), wrong + len(got - want), missed + len(want - got)
    print(f"matches vs ground truth: correct {right}, wrong {wrong}, missed {missed}")
    assert wrong == 0, "a face was matched to the wrong person"
    shutil.rmtree(tmp, ignore_errors=True)
    print("E2E OK")


if __name__ == "__main__":
    main()
