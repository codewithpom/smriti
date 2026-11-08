"""Link a person added without a reg number to their real record once the number is known.

  python pipeline/merge_people.py STU_N_rahul_das_1a2 S10123 [--data DIR]

Writes data/manual_aliases.json (faces.py applies it on the next fixrefs/match/build): the temporary
person disappears and its named faces count for the real person. If the real record has no name yet it
takes the temporary person's name. Prints counts only.
"""
import argparse
import json
import re
from pathlib import Path

import people_store

TEMP = re.compile(r"(STU|STAFF)_N_[a-z0-9_]{1,40}")


def merge(data, source, target):
    data = Path(data)
    meta = people_store.load(data / "people_meta.json", {})
    if not TEMP.fullmatch(source) or source not in meta:
        raise ValueError("first id must be a person added from the naming sheet (STU_N_... or STAFF_N_...)")
    if target not in meta:
        if not re.fullmatch(r"S\d{4,6}", target):
            raise ValueError("second id is not a known person or a reg number like S10123")
        people_store.add_person(data, target[1:])
    elif target == source:
        raise ValueError("ids are the same")
    if (source.startswith("STU_") and not target.startswith("S")) or (source.startswith("STAFF_") and not target.startswith("STAFF")):
        raise ValueError("a student can only be linked to a student and staff to staff")
    named = False
    if target.startswith("S") and not target.startswith("STAFF"):
        before = people_store.get_person(data, target[1:])
        after = people_store.add_person(data, target[1:], meta[source]["name"])
        named = before is not None and after["name"] != before["name"]
    path = data / "manual_aliases.json"
    aliases = people_store.load(path, {})
    aliases[source] = target
    people_store.save(path, aliases)
    return {"linked": 1, "named": int(named)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source"); ap.add_argument("target")
    ap.add_argument("--data", type=Path, default=people_store.DATA)
    a = ap.parse_args(argv)
    try:
        print(", ".join(f"{k} {v}" for k, v in merge(a.data, a.source, a.target).items()))
    except ValueError as error:
        print(f"error: {error}")
        return 2
    print("Now run faces.py fixrefs, faces.py match and build.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
