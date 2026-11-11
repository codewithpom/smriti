"""Exact and perceptual duplicate helpers for archive images."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

DUP_BITS = 6
SIMILAR_BITS = 12
ASPECT_TOL = 0.02
_SIZE = 32
# Quantizing C to 15 decimal places gives both runtimes the same IEEE-754
# coefficients, avoiding platform-libm differences at mathematically tied bits.
_COS = np.array([
    [round(math.sqrt(1 / 32) if k == 0 else math.sqrt(2 / 32) * math.cos(math.pi * (2 * n + 1) * k / 64), 15)
     for n in range(32)] for k in range(32)
], dtype=np.float64)


def gray_from_image(im):
    """Orient, cap, and convert an image to float64 luma."""
    image = ImageOps.exif_transpose(im).convert("RGB")
    if max(image.size) > 512:
        resampling = getattr(Image, "Resampling", Image).BILINEAR
        image.thumbnail((512, 512), resampling)
    rgb = np.asarray(image, dtype=np.float64)
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def _box32(gray):
    gray = np.asarray(gray, dtype=np.float64)
    height, width = gray.shape
    out = np.empty((32, 32), dtype=np.float64)
    for i in range(32):
        y0 = i * height // 32
        y1 = max((i + 1) * height // 32, y0 + 1)
        for j in range(32):
            x0 = j * width // 32
            x1 = max((j + 1) * width // 32, x0 + 1)
            total = 0.0
            count = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    total += gray[y, x]
                    count += 1
            out[i, j] = total / count
    return out


def phash_gray(gray):
    """Return a row-major 64-bit DCT perceptual hash as 16 hex digits."""
    x = _box32(gray)
    # Explicit accumulation order matches the browser implementation.
    temp = np.zeros((32, 32), dtype=np.float64)
    dct = np.zeros((32, 32), dtype=np.float64)
    for k in range(32):
        for j in range(32):
            total = 0.0
            for n in range(32):
                total += _COS[k, n] * x[n, j]
            temp[k, j] = total
    for k in range(32):
        for l in range(32):
            total = 0.0
            for n in range(32):
                total += temp[k, n] * _COS[l, n]
            dct[k, l] = total
    band = dct[:8, :8].reshape(-1)
    median = (float(np.partition(band, 31)[31]) + float(np.partition(band, 32)[32])) / 2
    value = 0
    for item in band:
        value = (value << 1) | (1 if item > median else 0)
    return f"{value:016x}"


def image_info(path):
    raw = Path(path).read_bytes()
    with Image.open(path) as im:
        oriented = ImageOps.exif_transpose(im)
        w, h = oriented.size
        gray = gray_from_image(oriented)
    return {"sha": hashlib.sha256(raw).hexdigest(), "ph": phash_gray(gray), "w": w, "h": h}


def hamming(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def classify(a, b):
    same_sha = bool(a.get("sha")) and bool(b.get("sha")) and a["sha"] == b["sha"]
    distance = hamming(a["ph"], b["ph"])
    aspect_ok = (a["h"] > 0 and b["h"] > 0 and
                 abs(a["w"] / a["h"] - b["w"] / b["h"]) / (b["w"] / b["h"]) <= ASPECT_TOL)
    if same_sha or (distance <= DUP_BITS and aspect_ok):
        return "duplicate"
    if distance <= SIMILAR_BITS:
        return "similar"
    return None


def find_match(item, index):
    best = None
    for entry in index:
        kind = classify(item, entry)
        if kind is None:
            continue
        candidate = (kind, hamming(item["ph"], entry["ph"]))
        if best is None or (kind == "duplicate" and best[0] != "duplicate") or (kind == best[0] and candidate[1] < best[1]):
            best = (kind, candidate[1], entry["id"])
    return (best[0], best[2]) if best else None


def group_duplicates(entries, score):
    parent = list(range(len(entries)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(entries):
        for j in range(i + 1, len(entries)):
            b = entries[j]
            if (a.get("sha") and a["sha"] == b.get("sha")) or hamming(a["ph"], b["ph"]) <= DUP_BITS:
                if classify(a, b) == "duplicate":
                    ri, rj = root(i), root(j)
                    if ri != rj:
                        parent[rj] = ri
    groups = {}
    for i, entry in enumerate(entries):
        groups.setdefault(root(i), []).append((i, entry))
    result = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        keeper = max(members, key=lambda pair: (score(pair[1]), -pair[0]))[1]["id"]
        for _, entry in members:
            if entry["id"] != keeper:
                result[entry["id"]] = keeper
    return result


def _vectors():
    rng = np.random.default_rng(73421)
    arrays = [
        ("gradient", np.fromfunction(lambda y, x: (x * 2.7 + y * 4.1 + x * y * 0.03) % 256, (32, 32), dtype=float)),
        ("checkerboard", np.fromfunction(lambda y, x: ((x // 4 + y // 4) % 2) * 255 + x * 0.23 + y * 0.17, (32, 32), dtype=float)),
        ("seeded_random", rng.random((37, 41)) * 255),
        ("tiny", rng.random((20, 13)) * 255),
        ("diagonal", np.fromfunction(lambda y, x: (x + y * 2) * 255 / 190, (48, 64), dtype=float)),
        ("constant", np.full((24, 29), 127.25)),
    ]
    return [{"name": name, "gray": rounded.tolist(), "hash": phash_gray(rounded)}
            for name, array in arrays for rounded in [np.round(array, 3)]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-vectors", action="store_true")
    args = parser.parse_args()
    if args.write_vectors:
        target = Path(__file__).resolve().parent / "testdata" / "phash_vectors.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_vectors(), separators=(",", ":")) + "\n", encoding="utf-8")
