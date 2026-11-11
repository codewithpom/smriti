"""Cheap, bounded face quality estimates for CPU extraction and legacy caches."""

import cv2
import numpy as np


def _clip(value):
    return float(np.clip(np.nan_to_num(value, nan=0.0), 0.0, 1.0))


def _combine(det, px, sharp, pose):
    s_det = _clip((det - 0.5) / 0.4)  # Detection confidence ramps from 0.5 to 0.9.
    s_size = _clip((px - 24) / 72)  # The shorter face side ramps from 24 to 96 pixels.
    return s_det ** 0.2 * s_size ** 0.35 * sharp ** 0.2 * pose ** 0.25


def quality(det, px, kps, crop_bgr):
    """Return quality in [0, 1] from detection, size, sharpness and five landmarks."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.size else None
    # Log Laplacian variance measures sharpness without letting outliers dominate.
    s_sharp = _clip(np.log10(cv2.Laplacian(gray, cv2.CV_64F).var() + 1) / 2.5) if gray is not None else 0.0
    points = np.asarray(kps, dtype=float)
    left, right, nose = points[:3]
    eye_distance = np.linalg.norm(right - left)
    yaw = abs((nose[0] - left[0]) - (right[0] - nose[0])) / max(eye_distance, 1e-8)
    s_pose = _clip(1 - yaw / 0.6)  # Nose displacement relative to the eyes penalises yaw.
    return float(_combine(det, px, s_sharp, s_pose))


def quality_without_crop(det, px):
    """Estimate legacy rows with neutral sharpness and pose scores of 0.7."""
    return float(_combine(det, px, 0.7, 0.7))
