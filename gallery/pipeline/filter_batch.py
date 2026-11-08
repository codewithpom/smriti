"""Select and copy photos with confident matches (high/confirmed).

With --people, count only students; otherwise count all recognised people.
"""

import argparse
import json
from pathlib import Path
import shutil


def select(catalog, faces, min_students=1, statuses=("confirmed", "high"), student_ids=None):
    kept = []
    counts = {"photos": len(catalog), "kept": 0, "no_faces": 0, "no_match": 0}
    for photo in catalog:
        photo_faces = faces.get(photo["id"], [])
        if not photo_faces:
            counts["no_faces"] += 1
            continue
        students = {
            face["person"] for face in photo_faces
            if face.get("status") in statuses and face.get("person")
            and (student_ids is None or face["person"] in student_ids)
        }
        if len(students) >= min_students:
            kept.append(photo["id"])
            counts["kept"] += 1
        else:
            counts["no_match"] += 1
    return kept, counts


def copy_kept(catalog, kept_ids, src, out):
    kept_ids = set(kept_ids)
    src, out = Path(src), Path(out)
    copied = 0
    for photo in catalog:
        if photo["id"] not in kept_ids:
            continue
        source, destination = src / photo["file"], out / photo["file"]
        if not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Reserve the destination exclusively so existing files stay untouched.
            with destination.open("xb"):
                pass
        except FileExistsError:
            continue
        try:
            shutil.copy2(source, destination)
        except FileNotFoundError:
            destination.unlink(missing_ok=True)
            if source.exists():
                raise
            continue
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        copied += 1
    return copied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "faces", "src", "out"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--people", type=Path, help="people_meta.json; count only ids with kind student")
    parser.add_argument("--min-students", type=int, default=1, help="minimum distinct confident people (students only with --people)")
    args = parser.parse_args(argv)
    with open(args.catalog, encoding="utf-8") as stream:
        catalog = json.load(stream)
    with open(args.faces, encoding="utf-8") as stream:
        faces = json.load(stream)
    student_ids = None
    if args.people is not None:
        people = json.loads(args.people.read_text(encoding="utf-8"))
        student_ids = {pid for pid, person in people.items() if person.get("kind") == "student"}
    kept, counts = select(catalog, faces, args.min_students, student_ids=student_ids)
    subject = "student" if student_ids is not None else "person"
    copy_kept(catalog, kept, args.src, args.out)
    print(
        f"kept {counts['kept']} of {counts['photos']} photos "
        f"({counts['no_faces']} without faces, "
        f"{counts['no_match']} without a confident {subject})"
    )


if __name__ == "__main__":
    main()
