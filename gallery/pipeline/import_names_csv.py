"""Import registration/name CSV files, printing counts only."""
import argparse
import csv
from itertools import chain
from pathlib import Path

from people_store import DATA, add_person, get_person, update_person, validate


REG_HEADERS = {"reg", "reg_no", "regn_no", "registration", "registration_no"}
NAME_HEADERS = {"name", "student_name", "full_name"}


def rows(stream):
    reader = csv.reader(stream)
    first = next(reader, None)
    if first is None:
        return
    headers = [c.strip().lower() for c in first]
    has_header = bool(set(headers) & (REG_HEADERS | NAME_HEADERS))
    if has_header:
        reg = next((i for i, c in enumerate(headers) if c in REG_HEADERS), None)
        name = next((i for i, c in enumerate(headers) if c in NAME_HEADERS), None)
        if reg is None or name is None:
            yield None
            return
        columns = {"reg_no": reg, "name": name}
        columns.update({c: headers.index(c) for c in ("section", "batch") if c in headers})
        source = reader
    else:
        columns = {"reg_no": 0, "name": 1}
        source = chain([first], reader)
    for row in source:
        if not row or not any(c.strip() for c in row):
            continue
        if (not has_header and len(row) != 2) or any(i >= len(row) for i in columns.values()):
            yield None
        else:
            yield {key: row[i].strip() for key, i in columns.items()}


def import_names(data_dir, stream, overwrite=False):
    counts = dict(created=0, named=0, kept_existing=0, invalid=0)
    for entry in rows(stream):
        try:
            if entry is None:
                raise ValueError("invalid row")
            reg, name, _ = validate(entry["reg_no"], entry["name"], entry.get("batch") or None)
            before = get_person(data_dir, reg)
            person = add_person(data_dir, reg, batch=entry.get("batch") or None) if before is None else before
            after = update_person(data_dir, person, name, entry.get("section"), overwrite)
            key = "created" if before is None else "named" if after["name"] != before["name"] else "kept_existing"
            counts[key] += 1
        except ValueError:
            counts["invalid"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    with args.csv.open(encoding="utf-8-sig", newline="") as stream:
        counts = import_names(args.data, stream, args.overwrite)
    print(", ".join(f"{key} {value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
