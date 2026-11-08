"""Local server: serves the gallery and saves Review answers to data/corrections.json.

  python pipeline/review_server.py      ->  http://127.0.0.1:8090/   (localhost only)

POST /api/answer  cluster|face: {qid,type,person:pid|"none"|"skip"}   scene: {qid,type,scene:id|"skip"}
POST /api/undo    revert the last answer
POST /api/rebuild re-run faces.py match + build.py (learning loop), takes a while
"""
import json
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import people_store

GALLERY = Path(__file__).resolve().parent.parent
DATA = GALLERY / "data"
PORT = 8090
lock = threading.Lock()


def jload(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def jsave(name, obj):
    tmp = DATA / (name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DATA / name)  # atomic: a crash never leaves a half-written file


def answer(body):
    qs, corr, meta = jload("questions.json", []), jload("corrections.json", {}), jload("people_meta.json", {})
    corr.setdefault("faces", {}); corr.setdefault("scenes", {}); corr.setdefault("skipped", []); corr.setdefault("history", [])
    q = next((x for x in qs if x["id"] == body.get("qid")), None)
    if q is None or q["type"] != body.get("type"):
        return 404, {"ok": False, "error": "unknown question"}
    entry = {"qid": q["id"], "type": q["type"], "question": q}
    if q["type"] == "scene":
        s = body.get("scene")
        if s == "skip":
            corr["skipped"].append(q["id"])
        elif s in q["all"]:
            corr["scenes"][q["photo"]] = s; entry["photo"] = q["photo"]
        else:
            return 400, {"ok": False, "error": "bad scene"}
    else:
        p = body.get("person")
        keys = q["faces"] if q["type"] == "cluster" else [f"{q['photo']}:{q['i']}"]
        if p == "skip":
            corr["skipped"].append(q["id"])
        elif p == "none" or p in meta:
            for k in keys:
                corr["faces"][k] = p
            entry["keys"] = keys
        else:
            return 400, {"ok": False, "error": "bad person"}
    corr["history"].append(entry)
    jsave("corrections.json", corr)
    jsave("questions.json", [x for x in qs if x["id"] != q["id"]])
    return 200, {"ok": True, "remaining": len(qs) - 1}


def undo():
    corr, qs = jload("corrections.json", {}), jload("questions.json", [])
    if not corr.get("history"):
        return 200, {"ok": True, "remaining": len(qs)}
    e = corr["history"].pop()
    for k in e.get("keys", []):
        corr["faces"].pop(k, None)
    if "photo" in e:
        corr["scenes"].pop(e["photo"], None)
    if e["qid"] in corr["skipped"]:
        corr["skipped"].remove(e["qid"])
    qs.append(e["question"]); qs.sort(key=lambda x: -x["priority"])
    jsave("corrections.json", corr); jsave("questions.json", qs)
    return 200, {"ok": True, "remaining": len(qs)}


def rebuild():
    for script in ("faces.py match", "build.py"):
        r = subprocess.run([sys.executable, "-I", str(GALLERY / "pipeline" / script.split()[0]), *script.split()[1:]],
                           capture_output=True, text=True)
        if r.returncode:
            return 500, {"ok": False, "error": r.stderr[-400:]}
    return 200, {"ok": True}


def addperson(body):
    try:
        if not isinstance(body, dict):
            raise ValueError("body must be an object")
        person = people_store.add_person(DATA, body.get("reg_no"), body.get("name", ""), body.get("batch"))
        return 200, {"ok": True, "person": person}
    except ValueError as error:
        return 400, {"ok": False, "error": str(error)}


class H(SimpleHTTPRequestHandler):
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            with lock:
                code, out = {"/api/addperson": lambda: addperson(body), "/api/answer": lambda: answer(body), "/api/undo": undo, "/api/rebuild": rebuild}.get(
                    self.path, lambda: (404, {"ok": False}))()
        except Exception as e:  # report, never crash the server
            code, out = 500, {"ok": False, "error": str(e)}
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store" if self.path.startswith(("/data/", "/api/")) else "max-age=3600")
        super().end_headers()


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}/")
    ThreadingHTTPServer(("127.0.0.1", PORT), partial(H, directory=str(GALLERY))).serve_forever()
