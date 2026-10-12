"""axis step: the vertical mirror axis of the figure (plus a small deskew angle).

Score of a candidate axis = IoU of the figure mask with its reflection about that axis. All candidate
x positions are scored at once per angle: the per-row autoconvolution of the mask (via FFT) gives the
reflected intersection count for every half-integer axis position. Pixel convention as in pairing.py:
pixel x reflects to floor(2 * axis_x - x - 0.5), so for axis_x = k / 2 the partner of x is k - 1 - x and
the intersection is autoconv[k - 1]. Coarse-to-fine: all angles at a reduced scale, then the best angle
at full resolution.
"""
from __future__ import annotations

import cv2
import numpy as np

DEFAULTS = {
    "axis_search_frac": 0.6,     # candidate axes span the middle fraction of the figure box width
    "axis_angle_max": 3.0,       # deskew angles searched in [-max, max] degrees ...
    "axis_angle_step": 0.5,      # ... in this step (0 disables the angle search)
    "axis_coarse_scale": 4,      # reduction factor for the coarse (all angles) pass
}


def mirror_index(w: int, axis_x: float) -> tuple[np.ndarray, np.ndarray]:
    """For every column x: the column its reflection lands in, and whether that is inside the image."""
    xs = np.arange(w)
    xr = np.floor(2.0 * float(axis_x) - xs - 0.5).astype(np.int64)
    return xr, (xr >= 0) & (xr < w)


def reflect_mask(mask: np.ndarray, axis_x: float) -> np.ndarray:
    """Mirror image of a mask (or label map) about a vertical axis, clipped to the image."""
    w = mask.shape[1]
    xr, valid = mirror_index(w, axis_x)
    out = np.zeros_like(mask)
    out[:, valid] = mask[:, xr[valid]]
    return out


def mirror_iou(mask: np.ndarray, axis_x: float) -> float:
    """Exact IoU of a mask with its (clipped) reflection about the axis."""
    m = mask.astype(bool)
    r = reflect_mask(m, axis_x)
    inter = int(np.count_nonzero(m & r))
    union = int(np.count_nonzero(m | r))
    return inter / union if union else 0.0


def rotate_mask(mask: np.ndarray, angle_deg: float, soft: bool = False) -> np.ndarray:
    """Rotate about the image centre (same convention as ingest.rotate_rgb). Float output when soft."""
    h, w = mask.shape
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), float(angle_deg), 1.0)
    src = mask.astype(np.float32)
    flags = cv2.INTER_LINEAR if soft else cv2.INTER_NEAREST
    out = cv2.warpAffine(src, m, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    return out if soft else out > 0.5


def iou_profile(mask: np.ndarray) -> np.ndarray:
    """IoU of the mask with its reflection for every axis_x = k / 2, k = 0 .. 2W (index k; 0 where undefined).

    The union counts reflected pixels that fall outside the image, which only matters for axes far from
    the centre of the figure.
    """
    m = np.asarray(mask, dtype=np.float32)
    h, w = m.shape
    n = 2 * w
    f = np.fft.rfft(m, n=n, axis=1)
    conv = np.fft.irfft((f * f).sum(axis=0), n=n)
    conv = np.maximum(np.rint(conv), 0.0) if mask.dtype == bool else np.maximum(conv, 0.0)
    area = float(m.sum())
    inter = np.zeros(n + 1, np.float64)
    inter[1:n + 1] = conv[0:n]                 # inter[k] = conv[k - 1]
    union = np.maximum(2.0 * area - inter, 1e-9)
    prof = inter / union
    prof[0] = 0.0
    return prof


def _best_in_range(profile: np.ndarray, x_min: float, x_max: float) -> tuple[float, float]:
    k_lo = int(np.ceil(2.0 * x_min))
    k_hi = int(np.floor(2.0 * x_max))
    k_lo = max(1, k_lo)
    k_hi = min(len(profile) - 1, k_hi)
    if k_hi < k_lo:
        k_lo = k_hi = int(np.clip(round(x_min + x_max), 1, len(profile) - 1))
    seg = profile[k_lo:k_hi + 1]
    k = k_lo + int(np.argmax(seg))
    return k / 2.0, float(profile[k])


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        h, w = mask.shape
        return 0, 0, w, h
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def find_axis(figure_mask: np.ndarray, params: dict | None = None, angles: list[float] | None = None) -> dict:
    """Search vertical axes (and small rotation angles) that best map the figure mask onto its reflection.

    Returns {"x", "angle_deg", "score", "mirror_iou", "search": {"x_min", "x_max", "angles"}} with x in the
    coordinates of the mask rotated by angle_deg (identical to the input coordinates when angle_deg == 0).
    """
    p = {**DEFAULTS, **(params or {})}
    mask = figure_mask.astype(bool)
    if angles is None:
        amax, step = float(p["axis_angle_max"]), float(p["axis_angle_step"])
        if step > 0 and amax > 0:
            n = int(round(amax / step))
            angles = [round(i * step, 4) for i in range(-n, n + 1)]
        else:
            angles = [0.0]
    frac = float(p["axis_search_frac"])

    def x_range(m: np.ndarray) -> tuple[float, float]:
        bx, _by, bw, _bh = _bbox(m)
        return bx + (1.0 - frac) / 2.0 * bw, bx + (1.0 + frac) / 2.0 * bw

    # coarse pass: every angle at reduced scale
    s = max(1, int(p["axis_coarse_scale"]))
    h, w = mask.shape
    if s > 1:
        small = cv2.resize(mask.astype(np.float32), (max(1, w // s), max(1, h // s)), interpolation=cv2.INTER_AREA)
    else:
        small = mask.astype(np.float32)
    best = None
    for ang in angles:
        rot = rotate_mask(small, ang, soft=True) if ang != 0.0 else small
        prof = iou_profile(rot)
        lo, hi = x_range(rot > 0.5)
        x, score = _best_in_range(prof, lo, hi)
        if best is None or score > best[0]:
            best = (score, float(ang), x)
    _cscore, angle, cx = best

    # fine pass: the best angle at full resolution, around the coarse optimum
    full = rotate_mask(mask, angle) if angle != 0.0 else mask
    prof = iou_profile(full)
    lo, hi = x_range(full)
    fx_lo, fx_hi = max(lo, cx * s - 2.0 * s), min(hi, cx * s + 2.0 * s)
    if fx_hi < fx_lo:
        fx_lo, fx_hi = lo, hi
    x, score = _best_in_range(prof, fx_lo, fx_hi)
    return {
        "x": float(x), "angle_deg": float(angle), "score": round(float(score), 5),
        "mirror_iou": round(mirror_iou(full, x), 5),
        "search": {"x_min": int(np.floor(lo)), "x_max": int(np.ceil(hi)), "angles": [float(a) for a in angles]},
    }
