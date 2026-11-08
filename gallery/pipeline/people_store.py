"""Validate and persist students added before the next gallery rebuild."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


DATA = Path(__file__).resolve().parent.parent / "data"


def load(path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate(reg_no, name="", batch=None):
    if not isinstance(reg_no, str) or not re.fullmatch(r"\d{4,6}", reg_no.strip()):
        raise ValueError("reg_no must contain 4 to 6 digits")
    reg_no = reg_no.strip()
    if not isinstance(name, str) or any(unicodedata.category(c) == "Cc" for c in name):
        raise ValueError("name must be text without control characters")
    name = " ".join(name.split())
    if len(name) > 60:
        raise ValueError("name must be at most 60 characters")
    if batch is not None and (not isinstance(batch, str) or not re.fullmatch(r"MB\d{2}", batch)):
        raise ValueError("batch must match MB followed by two digits")
    return reg_no, name, batch or ("MB28" if 10010 <= int(reg_no) <= 10335 else "MB27")


def unnamed(name):
    return not name or name.startswith("Reg ")


def get_person(data_dir, reg_no):
    data_dir = Path(data_dir)
    meta = load(data_dir / "people_meta.json", {})
    pid = "S" + reg_no
    entry = next((e for e in load(data_dir / "extra_students.json", []) if e["reg_no"] == reg_no), None)
    if pid not in meta and entry is None:
        return None
    person = {"id": pid, "kind": "student", "name": "", "reg_no": reg_no,
              "batch": validate(reg_no)[2], "section": None}
    person.update(meta.get(pid, {}))
    if entry is not None:
        for key in ("name", "batch", "section"):
            if key in entry:
                if key == "name" and unnamed(entry[key]) and not unnamed(person["name"]):
                    continue
                person[key] = entry[key]
    return person


def persist(data_dir, person, extra, extra_file="extra_students.json"):
    meta_path = data_dir / "people_meta.json"
    meta = load(meta_path, {})
    meta[person["id"]] = person
    people_path = data_dir / "people.json"
    people = load(people_path, []) if people_path.exists() else None
    if people is not None:
        existing = next((p for p in people if p["id"] == person["id"]), None)
        if existing is None:
            people.append({**person, "photos": 0, "avatar": None, "community": None, "friends": []})
        else:
            existing.update(person)
    save(data_dir / extra_file, extra)
    save(meta_path, meta)
    if people is not None:
        save(people_path, people)


def add_person(data_dir, reg_no, name="", batch=None):
    reg_no, name, batch = validate(reg_no, name, batch)
    data_dir = Path(data_dir)
    extra = load(data_dir / "extra_students.json", [])
    person = get_person(data_dir, reg_no)
    entry = next((e for e in extra if e["reg_no"] == reg_no), None)
    if person is not None:
        changed = False
        if name and unnamed(person["name"]):
            person["name"] = name
            if entry is not None:
                entry["name"] = name
                changed = True
        if changed or (entry is not None and person["id"] not in load(data_dir / "people_meta.json", {})):
            persist(data_dir, person, extra)
        return person
    person = {"id": "S" + reg_no, "kind": "student", "name": name,
              "reg_no": reg_no, "batch": batch, "section": None}
    extra.append({"reg_no": reg_no, "name": name, "section": None, "roll_sl": None, "batch": batch})
    persist(data_dir, person, extra)
    return person


def update_person(data_dir, person, name, section=None, overwrite=False):
    """Import a name/section, retaining real names unless explicitly replaced."""
    reg_no, name, _ = validate(person["reg_no"], name)
    if section is not None and not isinstance(section, str):
        raise ValueError("section must be text")
    updated = dict(person)
    if name and (overwrite or unnamed(person["name"])):
        updated["name"] = name
    if section and not updated.get("section"):
        updated["section"] = section.strip() or None
    extra = load(Path(data_dir) / "extra_students.json", [])
    entry = next((e for e in extra if e["reg_no"] == reg_no), None)
    if updated != person:
        if entry is None:
            extra.append({"reg_no": reg_no, "name": updated["name"], "section": updated["section"],
                          "roll_sl": None, "batch": updated["batch"]})
        else:
            entry.update(name=updated["name"], section=updated["section"])
        persist(Path(data_dir), updated, extra)
    return updated


def staff_key(name):
    """Same idea as faces.staff_aliases: ignore case, (brackets), punctuation and Sri/Shri."""
    name = re.sub(r"\bshri\b", "sri", re.sub(r"\([^)]*\)", "", name.lower()))
    return "".join(c for c in name if c.isalpha())


def add_staff(data_dir, name):
    """Add a staff member who has no reg number; returns the existing person if the name is already known."""
    if not isinstance(name, str) or any(unicodedata.category(c) == "Cc" for c in name):
        raise ValueError("name must be text without control characters")
    name = " ".join(name.split())
    if not 2 <= len(name) <= 60:
        raise ValueError("staff name must be 2 to 60 characters")
    data_dir = Path(data_dir)
    meta = load(data_dir / "people_meta.json", {})
    key = staff_key(name)
    for person in meta.values():
        if person.get("kind") == "staff" and staff_key(person.get("name", "")) == key:
            return person
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:26] or "x"
    pid = "STAFF_N_" + slug
    if pid in meta:   # a different name that makes the same slug
        pid += "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:4]
    extra = load(data_dir / "extra_staff.json", [])
    extra.append({"id": pid, "name": name, "role": ""})
    person = {"id": pid, "kind": "staff", "name": name, "role": ""}
    persist(data_dir, person, extra, "extra_staff.json")
    return person


def add_student_noreg(data_dir, name, batch="MB28"):
    """Add a student whose reg number is not known yet; link them later with merge_people.py."""
    if not isinstance(name, str) or any(unicodedata.category(c) == "Cc" for c in name):
        raise ValueError("name must be text without control characters")
    name = " ".join(name.split())
    if not 2 <= len(name) <= 60:
        raise ValueError("student name must be 2 to 60 characters")
    if not isinstance(batch, str) or not re.fullmatch(r"MB\d{2}", batch):
        raise ValueError("batch must match MB followed by two digits")
    data_dir = Path(data_dir)
    meta = load(data_dir / "people_meta.json", {})
    key = staff_key(name)
    for person in meta.values():
        if person.get("kind") == "student" and person.get("batch") == batch and staff_key(person.get("name", "")) == key and key:
            return person
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:26] or "x"
    pid = "STU_N_" + slug
    if pid in meta:
        pid += "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:4]
    extra = load(data_dir / "extra_students_noreg.json", [])
    extra.append({"id": pid, "name": name, "batch": batch})
    person = {"id": pid, "kind": "student", "name": name, "reg_no": "", "batch": batch, "section": None}
    persist(data_dir, person, extra, "extra_students_noreg.json")
    return person
