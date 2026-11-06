"""Tally Firestore votes and merge settled answers into corrections.json."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"


def tally(questions, votes, users, overrides=None):
    """Return settled answers and disputed question ids from fabricated or live records."""
    by_id = {q["id"]: q for q in questions}
    weights = {qid: {} for qid in by_id}
    for vote in votes:
        qid, uid, answer = vote.get("qid"), vote.get("uid"), vote.get("answer")
        user = users.get(uid, {})
        if qid not in by_id or answer == "skip" or user.get("status") != "approved":
            continue
        q = by_id[qid]
        weight = 2 if q.get("type") in ("face", "cluster") and answer == user.get("pid") and user.get("pid") in q.get("cands", []) else 1
        weights[qid][answer] = weights[qid].get(answer, 0) + weight
    overrides = overrides or {}
    settled, disputed = {}, []
    for qid, tally_map in weights.items():
        if qid in overrides:
            settled[qid] = overrides[qid]
            continue
        ranked = sorted(tally_map.items(), key=lambda pair: (-pair[1], pair[0]))
        if not ranked:
            continue
        top = ranked[0][1]
        second = ranked[1][1] if len(ranked) > 1 else 0
        total = sum(tally_map.values())
        if top >= 3 and top >= 2 * second:
            settled[qid] = ranked[0][0]
        elif total >= 5:
            disputed.append(qid)
    return {"settled": settled, "disputed": disputed}


def corrections_for(questions, settled, existing):
    out = {**existing, "faces": dict(existing.get("faces", {})), "scenes": dict(existing.get("scenes", {})),
           "skipped": list(existing.get("skipped", [])), "history": list(existing.get("history", []))}
    for q in questions:
        answer = settled.get(q["id"])
        if answer is None:
            continue
        if answer == "skip":
            if q["id"] not in out["skipped"]:
                out["skipped"].append(q["id"])
        elif q["type"] == "scene":
            out["scenes"][q["photo"]] = answer
        else:
            keys = q["faces"] if q["type"] == "cluster" else [f"{q['photo']}:{q['i']}"]
            for key in keys:
                out["faces"][key] = answer
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        parser.error("GOOGLE_APPLICATION_CREDENTIALS must point to a service-account JSON")
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]))
    db = firestore.client()
    users = {d.id: d.to_dict() for d in db.collection("users").stream()}
    votes = [d.to_dict() for d in db.collection("votes").stream()]
    overrides = {d.id: d.to_dict()["answer"] for d in db.collection("overrides").stream()}
    result = tally(questions, votes, users, overrides)
    if args.dry_run:
        print(f"settled={len(result['settled'])} disputed={len(result['disputed'])}")
        return
    db.document("meta/progress").set({"settled": {qid: True for qid in result["settled"]},
                                      "disputed": result["disputed"],
                                      "updatedAt": firestore.SERVER_TIMESTAMP})
    path = DATA / "corrections.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = corrections_for(questions, result["settled"], existing)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    print(f"settled={len(result['settled'])} disputed={len(result['disputed'])} updatedAt={datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
