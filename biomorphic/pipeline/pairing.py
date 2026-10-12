"""Sides (L / R / axis) and mirror twins for the regions of one level.

Baseline implementation: rigid reflection about the vertical axis, all pairwise reflected
intersections computed in one vectorised pass, one-to-one greedy assignment, Lab colour rejection.
The pipeline work package extends this with local registration (translation, small scale) so that
hand-drawn twins score higher than the rigid IoU ceiling of about 0.5.
"""
from __future__ import annotations

from typing import Any

import numpy as np

DEFAULTS = {
    "pair_min_iou": 0.15,        # below this a candidate is not a twin
    "pair_max_delta_e": 30.0,    # mean-Lab distance above which a geometric match is rejected
    "axis_self_iou": 0.5,        # a region whose mirror overlaps itself this much sits on the axis
}


def reflected_intersections(label_map: np.ndarray, axis_x: float) -> dict[tuple[int, int], int]:
    """Pixel counts of region a overlapping the mirror image of region b, for every (a, b) that overlap."""
    h, w = label_map.shape
    xs = np.arange(w)
    # pixel x covers [x, x+1); its centre mirrors to 2*axis_x - x - 0.5, which lies in pixel floor(...)
    xr = np.floor(2.0 * axis_x - xs - 0.5).astype(np.int64)
    valid = (xr >= 0) & (xr < w)
    mirrored = np.zeros_like(label_map)
    mirrored[:, valid] = label_map[:, xr[valid]]
    a = label_map.astype(np.int64)
    b = mirrored.astype(np.int64)
    m = (a > 0) & (b > 0)
    keys = (a[m] << 32) | b[m]
    uk, cnt = np.unique(keys, return_counts=True)
    return {(int(k >> 32), int(k & 0xFFFFFFFF)): int(c) for k, c in zip(uk.tolist(), cnt.tolist())}


def pair_regions(label_map: np.ndarray, axis_x: float, lab_image: np.ndarray, regions: dict[int, Any],
                 params: dict | None = None) -> dict[str, Any]:
    """Return {"sides": {id: "L"|"R"|"axis"}, "pairs": [{"a","b","iou","dx","dy"}], "twin": {id: id}}."""
    p = {**DEFAULTS, **(params or {})}
    inter = reflected_intersections(label_map, axis_x)
    sides: dict[int, str] = {}
    for rid, info in regions.items():
        self_i = inter.get((rid, rid), 0)
        self_iou = self_i / max(1, 2 * info.area - self_i)
        if self_iou >= p["axis_self_iou"]:
            sides[rid] = "axis"
        else:
            sides[rid] = "L" if info.centroid[0] < axis_x else "R"
    candidates = []
    for (a, b), c in inter.items():
        if a == b or sides.get(a) != "L" or sides.get(b) != "R":
            continue
        ia, ib = regions[a], regions[b]
        iou = c / max(1, ia.area + ib.area - c)
        if iou < p["pair_min_iou"]:
            continue
        de = float(np.linalg.norm(np.asarray(ia.mean_lab) - np.asarray(ib.mean_lab)))
        if de > p["pair_max_delta_e"]:
            continue
        candidates.append((iou, a, b, de))
    candidates.sort(reverse=True)
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for iou, a, b, de in candidates:
        if a in used_a or b in used_b:
            continue
        used_a.add(a)
        used_b.add(b)
        pairs.append({"a": a, "b": b, "iou": round(float(iou), 4), "dx": 0.0, "dy": 0.0, "delta_e": round(de, 2)})
    twin = {}
    for pr in pairs:
        twin[pr["a"]] = pr["b"]
        twin[pr["b"]] = pr["a"]
    return {"sides": sides, "pairs": pairs, "twin": twin}
