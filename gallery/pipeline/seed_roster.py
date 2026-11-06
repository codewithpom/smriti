"""Seed Firestore roster from local people JSON files."""
import argparse
import json
import os
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
STUDENT_PID = re.compile(r"^S(\d+)$")


def records(value):
    if isinstance(value, dict):
        return [(str(k), v) for k, v in value.items()]
    if isinstance(value, list):
        return [(str(v.get("pid", v.get("id", ""))), v) for v in value if isinstance(v, dict)]
    raise ValueError("expected an object or list")


def prepare(people, extras):
    roster, mismatches = {}, []
    for source in (people, extras):
        for key, item in records(source):
            if not isinstance(item, dict):
                continue
            pid = str(item.get("pid", item.get("id", key)))
            match = STUDENT_PID.fullmatch(pid)
            if not match:
                continue
            regno = str(item.get("regno", item.get("reg_no", match.group(1))))
            if regno != match.group(1):
                mismatches.append((pid, regno))
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                mismatches.append((pid, "missing-name"))
                continue
            if regno in roster and roster[regno]["pid"] != pid:
                mismatches.append((pid, regno))
                continue
            roster[regno] = {"pid": pid, "name": name}
    return roster, mismatches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    people = json.loads((DATA / "people.json").read_text(encoding="utf-8"))
    extras = json.loads((DATA / "extra_students.json").read_text(encoding="utf-8"))
    roster, mismatches = prepare(people, extras)
    if args.dry_run:
        print(f"roster={len(roster)} mismatches={len(mismatches)}")
        return
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        parser.error("GOOGLE_APPLICATION_CREDENTIALS must point to a service-account JSON")
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]))
    db = firestore.client()
    items = list(roster.items())
    for start in range(0, len(items), 400):
        batch = db.batch()
        for regno, item in items[start:start + 400]:
            batch.set(db.collection("roster").document(regno), item)
        batch.commit()
    print(f"written={len(roster)} mismatches={len(mismatches)}")
    for pid, regno in mismatches:
        print(f"mismatch pid={pid} regno={regno}")


if __name__ == "__main__":
    main()
