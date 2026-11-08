"""Evaluate automatic face matches with leave-one-out confirmed faces."""

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from faces import DATA, MIN_FACE_PX, T_HIGH, T_MARGIN, decide, rank_persons


def leave_one_out_trials(raw, emb, ref_emb, ref_ids, corrections, bank="refs+confirmed", agg="max"):
    """Return ranked trials and anonymous counts, without changing inputs."""
    if bank not in ("refs", "refs+confirmed"):
        raise ValueError("unknown bank")
    truth = corrections.get("faces", {})
    confirmed = [
        (f"{row['photo']}:{i}", truth[f"{row['photo']}:{i}"], f)
        for row in raw for i, f in enumerate(row["faces"])
        if truth.get(f"{row['photo']}:{i}") not in (None, "none")
    ]
    bank_e, bank_p = list(ref_emb), list(ref_ids)
    if bank == "refs+confirmed":
        bank_e.extend(emb[f["e"]] for _, _, f in confirmed)
        bank_p.extend(pid for _, pid, _ in confirmed)
    bank_e = np.asarray(bank_e, dtype=emb.dtype).reshape(-1, emb.shape[1])
    bank_p = np.asarray(bank_p)
    counts = Counter(pid for _, pid, _ in confirmed)
    stats = {
        "total": len(confirmed), "unreachable": 0,
        "tiny": sum(f["px"] < MIN_FACE_PX for _, _, f in confirmed),
        "people": len(counts),
        "biased": bool(counts and max(counts.values()) > len(confirmed) / 2),
    }
    # A cluster answer labels all its faces at once, and cluster-mates look alike by construction, so
    # holding out one face while keeping its cluster-mates would score nothing. Hold the whole answer out.
    group = {}
    for h, entry in enumerate(corrections.get("history", [])):
        for key in (entry.get("keys") or []) if isinstance(entry, dict) else []:
            group[key] = ("answer", h)
    gids = np.array([str(group.get(key, key)) for key, _, _ in confirmed])
    trials = []
    for n, (_, pid, f) in enumerate(confirmed):
        keep = np.ones(len(bank_p), dtype=bool)
        if bank == "refs+confirmed":
            keep[len(ref_ids):] = gids != gids[n]
        if not np.any(bank_p[keep] == pid):
            stats["unreachable"] += 1
            continue
        ranked = rank_persons(bank_e[keep], bank_p[keep], emb[f["e"]], agg)
        trials.append((pid, f["px"], ranked))
    return trials, stats


def sweep(trials, thresholds, t_margin=T_MARGIN, t_high=T_HIGH, axis="high"):
    """Count high decisions at each threshold; unreachable trials are omitted."""
    if axis not in ("high", "margin"):
        raise ValueError("unknown sweep axis")
    rows = []
    for threshold in thresholds:
        correct = wrong = 0
        for truth, px, ranked in trials:
            person, _, status = decide(
                ranked, px,
                t_high=threshold if axis == "high" else t_high,
                t_margin=threshold if axis == "margin" else t_margin,
            )
            if status == "high":
                correct += person == truth
                wrong += person != truth
        accepted = correct + wrong
        rows.append({
            "threshold": threshold, "accepted": accepted,
            "correct": correct, "wrong": wrong,
            "precision": correct / accepted if accepted else 0.0,
            "recall": correct / len(trials) if trials else 0.0,
        })
    return rows


def target_line(bank, agg, rows, target):
    eligible = [r["threshold"] for r in rows if r["accepted"] and r["precision"] >= target]
    result = f"lowest T_HIGH={min(eligible):.2f}" if eligible else "none reaches target"
    return f"{bank} / {agg}: {result} (target={target:.3f})"


def print_table(label, rows):
    print(f"{label:>8} accepted correct wrong precision recall")
    for r in rows:
        print(f"{r['threshold']:8.2f} {r['accepted']:8d} {r['correct']:7d} {r['wrong']:5d} "
              f"{r['precision']:9.3f} {r['recall']:6.3f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--agg", choices=("max", "top3", "both"), default="both")
    parser.add_argument("--bank", choices=("refs", "refs+confirmed", "both"), default="both")
    parser.add_argument("--target", type=float, default=0.98)
    args = parser.parse_args(argv)
    if not 0 <= args.target <= 1:
        parser.error("--target must be between 0 and 1")
    def load(name):
        with (args.data / name).open(encoding="utf-8") as stream:
            return json.load(stream)
    raw, ref_ids = load("faces_raw.json"), load("ref_ids.json")
    path = args.data / "corrections.json"
    corrections = load("corrections.json") if path.exists() else {}
    emb = np.load(args.data / "face_emb.npy", allow_pickle=False)
    ref_emb = np.load(args.data / "ref_emb.npy", allow_pickle=False)
    banks = ("refs", "refs+confirmed") if args.bank == "both" else (args.bank,)
    aggs = ("max", "top3") if args.agg == "both" else (args.agg,)
    for bank in banks:
        for agg in aggs:
            trials, stats = leave_one_out_trials(raw, emb, ref_emb, ref_ids, corrections, bank, agg)
            print(f"{bank} / {agg}: total={stats['total']} unreachable={stats['unreachable']} "
                  f"tiny={stats['tiny']} people={stats['people']}")
            if stats["biased"]:
                print("WARNING: one person supplies more than 50% of trials; truth is biased.")
            rows = sweep(trials, [n / 100 for n in range(30, 71, 2)])
            print_table("T_HIGH", rows)
            print_table("T_MARGIN", sweep(trials, (0.0, 0.03, 0.06, 0.10), axis="margin"))
            print(target_line(bank, agg, rows, args.target))


if __name__ == "__main__":
    main()
