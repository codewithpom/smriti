"""Import admin-exported students, printing counts only."""
import argparse
from pathlib import Path

from people_store import DATA, add_person, get_person, load, validate


def import_people(data_dir, entries):
    counts = dict(created=0, updated_name=0, unchanged=0, invalid=0)
    if not isinstance(entries, list):
        counts["invalid"] = 1
        return counts
    for entry in entries:
        try:
            if not isinstance(entry, dict):
                raise ValueError("entry must be an object")
            reg, name, _ = validate(entry.get("reg_no"), entry.get("name", ""), entry.get("batch"))
            before = get_person(data_dir, reg)
            after = add_person(data_dir, reg, name, entry.get("batch"))
            key = "created" if before is None else "updated_name" if after["name"] != before["name"] else "unchanged"
            counts[key] += 1
        except ValueError:
            counts["invalid"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extra", type=Path)
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    counts = import_people(args.data, load(args.extra, []))
    print(", ".join(f"{key} {value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
