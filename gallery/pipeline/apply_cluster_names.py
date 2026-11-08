"""Apply downloaded cluster names through the existing review handler."""
import argparse
import re
from pathlib import Path

import review_server
import people_store
from name_sheet import DATA, cluster_members, fingerprint, load


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("answers", type=Path)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    document = load(args.answers)
    groups = cluster_members(load(args.data / "faces.json"))
    ids = document.get("clusters", list(groups))
    selected = {cid: groups[cid] for cid in ids if cid in groups}
    if len(selected) != len(ids) or fingerprint(selected) != document.get("fingerprint"):
        print("data changed since the sheet was made, regenerate it")
        return 2
    meta = load(args.data / "people_meta.json")
    questions = {q["id"]: q for q in load(args.data / "questions.json")}
    counts = dict(applied=0, skipped=0, unknown=0, missing=0, created=0)
    new_name = lambda pid: str(((document.get("people") or {}).get(pid) or {}).get("name", "")).strip()[:60]
    previous = review_server.DATA
    try:
        review_server.DATA = args.data
        for cid, person in document["answers"].items():
            if person == "skip":
                counts["skipped"] += 1
            elif person != "none" and person not in meta and not (isinstance(person, str) and (re.fullmatch(r"S\d{4,6}", person) or (
                    re.fullmatch(r"(STAFF|STU)_N_[a-z0-9_]{1,40}", person) and len(new_name(person)) >= 2))):
                counts["unknown"] += 1
            elif cid not in selected or f"c{cid}" not in questions:
                counts["missing"] += 1
            elif questions[f"c{cid}"].get("type") != "cluster" or set(questions[f"c{cid}"].get("faces", [])) != set(selected[cid]):
                counts["missing"] += 1
            elif args.dry_run:
                counts["applied"] += 1
            else:
                with review_server.lock:
                    if person.startswith(("STAFF_N_", "STU_N_")) and person not in meta:
                        if person.startswith("STAFF_N_"):
                            made = people_store.add_staff(args.data, new_name(person))
                        else:
                            batch = str(((document.get("people") or {}).get(person) or {}).get("batch") or "MB28")
                            made = people_store.add_student_noreg(args.data, new_name(person), batch if re.fullmatch(r"MB\d{2}", batch) else "MB28")
                        counts["created"] += made["id"] not in meta   # an existing staff with the same name is reused
                        meta[made["id"]] = made
                        person = made["id"]
                    elif person != "none" and person not in meta:
                        existing = people_store.get_person(args.data, person[1:])
                        meta[person] = people_store.add_person(args.data, person[1:], new_name(person))
                        if existing is None:
                            counts["created"] += 1
                    code, _ = review_server.answer({"qid": f"c{cid}", "type": "cluster", "person": person})
                counts["applied" if code == 200 else "missing"] += 1
    finally:
        review_server.DATA = previous
    print(", ".join(f"{key} {value}" for key, value in counts.items()))
    print("Run faces.py match and build.py after applying answers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
