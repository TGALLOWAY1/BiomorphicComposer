"""Sides (L / R / axis) and mirror twins for the regions of one level.

Sides: a region whose rigid mirror image overlaps itself (IoU >= axis_self_iou) sits on the axis; the rest
are L or R by centroid. Twins: for each L region, the R regions whose bbox meets the reflected L bbox
(expanded by the registration radius) and whose area is comparable are candidates; each candidate is
scored by **local registration**: the reflected L mask is shifted (and optionally rescaled) to maximise
its IoU with the R mask. The shift search is a single FFT cross-correlation per candidate, so every
translation in the window is scored at once. Candidates with a registered IoU below pair_min_iou or a
mean-Lab distance above pair_max_delta_e are rejected; the rest are assigned one-to-one, best first.

Reported dx, dy are the shift (working px) of the reflected L mask that best overlays the R mask.

pair_regions_fold is the method used when the session has a fold (pipeline/fold.py): the other half is laid on
top of this one, so every region is compared directly with the regions its pixels' twins belong to. Shape
score = IoU of the region with the folded twin after a small residual shift; colour = distance between the
mean Lab colours, plus the per-pixel colour difference over the overlap (the colour pattern); assignment is
one-to-one and globally optimal (Hungarian) rather than greedy. A region is central when it folds onto itself
and lies on the centre band.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from scipy.signal import fftconvolve

DEFAULTS = {
    "pair_min_iou": 0.35,        # registered IoU below this: not a twin
    "pair_max_delta_e": 30.0,    # mean-Lab distance above which a geometric match is rejected
    "axis_self_iou": 0.5,        # a region whose rigid mirror overlaps itself this much sits on the axis
    "pair_shift_frac": 0.25,     # registration radius = this fraction of the larger bbox side ...
    "pair_shift_min_px": 6,      # ... clamped to [min, max] working px
    "pair_shift_max_px": 60,
    "pair_scales": [1.0],        # scales tried for the reflected mask (e.g. [0.9, 1.0, 1.1] for +-10 %)
    "pair_area_ratio_max": 3.0,  # candidates must have areas within this ratio
    "pair_max_candidates": 5,    # registrations per L region (best rigid overlaps / nearest first)
    "pair_fft_max_side": 320,    # registration frames larger than this are searched at reduced scale, then refined
    "fold_shift_px": 10,         # fold pairing: residual shift searched around the fold (working px)
    "fold_candidates": 4,        # fold pairing: twins scored per region (largest folded overlaps first)
    "fold_colour_sigma": 18.0,   # fold pairing: colour factor exp(-(dE / sigma)^2) on the mean-Lab distance
    "fold_pattern_sigma": 30.0,  # fold pairing: same on the mean per-pixel colour difference over the overlap
}


def mirror_index(w: int, axis_x: float) -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(w)
    # pixel x covers [x, x+1); its centre mirrors to 2*axis_x - x - 0.5, which lies in pixel floor(...)
    xr = np.floor(2.0 * float(axis_x) - xs - 0.5).astype(np.int64)
    return xr, (xr >= 0) & (xr < w)


def reflected_intersections(label_map: np.ndarray, axis_x: float) -> dict[tuple[int, int], int]:
    """Pixel counts of region a overlapping the mirror image of region b, for every (a, b) that overlap."""
    h, w = label_map.shape
    xr, valid = mirror_index(w, axis_x)
    mirrored = np.zeros_like(label_map)
    mirrored[:, valid] = label_map[:, xr[valid]]
    # dense ids (region ids can be large, e.g. manual groups) so that one bincount over id pairs does the job
    present = np.bincount(label_map.ravel())
    ids = np.nonzero(present)[0]
    if ids.size == 0 or ids[0] != 0:
        ids = np.concatenate([[0], ids]).astype(np.int64)
    k = len(ids)
    lut = np.zeros(int(ids[-1]) + 1, np.int64)
    lut[ids] = np.arange(k)
    a = lut[label_map]
    b = lut[mirrored]
    m = (a > 0) & (b > 0)
    cnt = np.bincount(a[m] * k + b[m], minlength=k * k)
    found = np.nonzero(cnt)[0]
    return {(int(ids[f // k]), int(ids[f % k])): int(cnt[f]) for f in found.tolist()}


def reflect_bbox(bbox: tuple[int, int, int, int], axis_x: float, w_img: int) -> tuple[int, int, int, int]:
    """bbox (x, y, w, h) of the reflected region in the pairing pixel convention."""
    x, y, w, h = bbox
    xs = np.array([x, x + w - 1])
    xr = np.floor(2.0 * float(axis_x) - xs - 0.5).astype(np.int64)
    x0, x1 = int(xr.min()), int(xr.max())
    return x0, y, x1 - x0 + 1, h


def _shift_window_iou(corr: np.ndarray, area_a: int, area_b: int, cy: int, cx: int, r: int) -> tuple[float, int, int]:
    """Best IoU within |dy|,|dx| <= r of a correlation surface whose zero shift is at (cy, cx)."""
    y0, y1 = max(0, cy - r), min(corr.shape[0], cy + r + 1)
    x0, x1 = max(0, cx - r), min(corr.shape[1], cx + r + 1)
    win = corr[y0:y1, x0:x1]
    inter = np.clip(np.rint(win), 0, min(area_a, area_b))
    iou = inter / np.maximum(1.0, area_a + area_b - inter)
    i = int(np.argmax(iou))
    iy, ix = divmod(i, iou.shape[1])
    return float(iou.flat[i]), int(y0 + iy - cy), int(x0 + ix - cx)


def _shifted_iou(a: np.ndarray, b: np.ndarray, dy: int, dx: int, area_a: int, area_b: int) -> float:
    """Exact IoU of mask a shifted by (dy, dx) against mask b (same frame)."""
    h, w = a.shape
    ay0, ay1 = max(0, -dy), min(h, h - dy)
    ax0, ax1 = max(0, -dx), min(w, w - dx)
    if ay1 <= ay0 or ax1 <= ax0:
        return 0.0
    inter = int(np.count_nonzero(a[ay0:ay1, ax0:ax1] & b[ay0 + dy:ay1 + dy, ax0 + dx:ax1 + dx]))
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def register_masks(a: np.ndarray, b: np.ndarray, max_shift: int, fft_max_side: int = 512) -> tuple[float, int, int]:
    """Shift (dy, dx), |.| <= max_shift, of mask a that maximises its IoU with mask b (same frame).

    Returns (iou, dy, dx). Large frames are searched at reduced resolution, then refined exactly.
    """
    a = a.astype(bool)
    b = b.astype(bool)
    area_a, area_b = int(a.sum()), int(b.sum())
    if area_a == 0 or area_b == 0:
        return 0.0, 0, 0
    h, w = a.shape
    f = int(np.ceil(max(h, w) / float(fft_max_side)))
    if f > 1:
        # coarse search at 1/f scale, then an exact 3x3 refinement (residual error < f px, negligible
        # for the IoU of regions this large)
        hs, ws = max(1, h // f), max(1, w // f)
        a_s = cv2.resize(a.astype(np.float32), (ws, hs), interpolation=cv2.INTER_AREA)
        b_s = cv2.resize(b.astype(np.float32), (ws, hs), interpolation=cv2.INTER_AREA)
        corr = fftconvolve(b_s, a_s[::-1, ::-1], mode="full")
        _iou, dy, dx = _shift_window_iou(corr, float(a_s.sum()), float(b_s.sum()), hs - 1, ws - 1,
                                         max(1, int(np.ceil(max_shift / f))))
        best = (0.0, 0, 0)
        step = max(1, f // 2)
        for ddy in (dy * f - step, dy * f, dy * f + step):
            for ddx in (dx * f - step, dx * f, dx * f + step):
                if abs(ddy) > max_shift or abs(ddx) > max_shift:
                    continue
                v = _shifted_iou(a, b, ddy, ddx, area_a, area_b)
                if v > best[0]:
                    best = (v, ddy, ddx)
        return best
    corr = fftconvolve(b.astype(np.float32), a[::-1, ::-1].astype(np.float32), mode="full")
    return _shift_window_iou(corr, area_a, area_b, h - 1, w - 1, int(max_shift))


def _scaled(mask: np.ndarray, scale: float) -> np.ndarray:
    """Rescale a mask about its centroid inside the same frame."""
    if scale == 1.0:
        return mask
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    cy, cx = float(ys.mean()), float(xs.mean())
    m = np.array([[scale, 0.0, cx - scale * cx], [0.0, scale, cy - scale * cy]], np.float32)
    out = cv2.warpAffine(mask.astype(np.uint8), m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    return out > 0


def register_pair(label_map: np.ndarray, a: int, bbox_a: tuple, b: int, bbox_b: tuple, axis_x: float,
                  max_shift: int, scales: list[float], fft_max_side: int) -> dict:
    """Registered IoU of region a's reflection against region b, with the best shift and scale."""
    h, w = label_map.shape
    rb_a = reflect_bbox(bbox_a, axis_x, w)
    grow = int(max_shift + (max(scales) - 1.0) * max(rb_a[2], rb_a[3]) + 2)
    x0 = max(0, min(rb_a[0], bbox_b[0]) - grow)
    y0 = max(0, min(rb_a[1], bbox_b[1]) - grow)
    x1 = min(w, max(rb_a[0] + rb_a[2], bbox_b[0] + bbox_b[2]) + grow)
    y1 = min(h, max(rb_a[1] + rb_a[3], bbox_b[1] + bbox_b[3]) + grow)
    sub = label_map[y0:y1, x0:x1]
    mask_b = sub == b
    # reflected mask of a inside the frame: frame column c (image column x0 + c) reflects to image column xr
    xs = np.arange(x0, x1)
    xr = np.floor(2.0 * float(axis_x) - xs - 0.5).astype(np.int64)
    valid = (xr >= 0) & (xr < w)
    ref_a = np.zeros(sub.shape, bool)
    ref_a[:, valid] = label_map[y0:y1, xr[valid]] == a
    best = {"iou": 0.0, "dx": 0, "dy": 0, "scale": 1.0}
    for s in scales:
        m = _scaled(ref_a, float(s))
        iou, dy, dx = register_masks(m, mask_b, max_shift, fft_max_side)
        if iou > best["iou"]:
            best = {"iou": float(iou), "dx": int(dx), "dy": int(dy), "scale": float(s)}
    return best


def pair_regions(label_map: np.ndarray, axis_x: float, lab_image: np.ndarray, regions: dict[int, Any],
                 params: dict | None = None) -> dict[str, Any]:
    """Return {"sides": {id: "L"|"R"|"axis"}, "pairs": [{"a","b","iou","dx","dy",...}], "twin": {id: id}}."""
    p = {**DEFAULTS, **(params or {})}
    scales = [float(s) for s in (p["pair_scales"] if isinstance(p["pair_scales"], (list, tuple)) else [1.0])]
    if 1.0 not in scales:
        scales.append(1.0)
    inter = reflected_intersections(label_map, axis_x)
    sides: dict[int, str] = {}
    for rid, info in regions.items():
        self_i = inter.get((rid, rid), 0)
        self_iou = self_i / max(1, 2 * info.area - self_i)
        if self_iou >= p["axis_self_iou"]:
            sides[rid] = "axis"
        else:
            sides[rid] = "L" if info.centroid[0] < axis_x else "R"
    w_img = label_map.shape[1]
    left = [rid for rid, s in sides.items() if s == "L"]
    right = [rid for rid, s in sides.items() if s == "R"]
    ratio_max = float(p["pair_area_ratio_max"])
    candidates: list[tuple[float, int, int, float, dict]] = []
    if left and right:
        r_ids = np.array(right)
        r_bbox = np.array([regions[r].bbox for r in right], dtype=np.int64).reshape(-1, 4)
        r_area = np.array([regions[r].area for r in right], dtype=np.float64)
        r_cent = np.array([regions[r].centroid for r in right], dtype=np.float64).reshape(-1, 2)
        for a in left:
            ia = regions[a]
            bx, by, bw, bh = reflect_bbox(ia.bbox, axis_x, w_img)
            shift = int(np.clip(round(p["pair_shift_frac"] * max(bw, bh)), p["pair_shift_min_px"],
                                p["pair_shift_max_px"]))
            ex0, ey0, ex1, ey1 = bx - shift, by - shift, bx + bw + shift, by + bh + shift
            ok = (r_bbox[:, 0] < ex1) & (r_bbox[:, 0] + r_bbox[:, 2] > ex0) & \
                 (r_bbox[:, 1] < ey1) & (r_bbox[:, 1] + r_bbox[:, 3] > ey0)
            ratio = np.maximum(r_area / ia.area, ia.area / r_area)
            ok &= ratio <= ratio_max
            if not ok.any():
                continue
            rcx = 2.0 * axis_x - ia.centroid[0]
            dist = np.hypot(r_cent[:, 0] - rcx, r_cent[:, 1] - ia.centroid[1])
            rigid = np.array([inter.get((int(b), a), 0) for b in r_ids], dtype=np.float64)
            order = sorted(np.nonzero(ok)[0].tolist(), key=lambda i: (-rigid[i], dist[i]))
            for i in order[: int(p["pair_max_candidates"])]:
                b = int(r_ids[i])
                ib = regions[b]
                reg = register_pair(label_map, a, ia.bbox, b, ib.bbox, axis_x, shift, scales,
                                    int(p["pair_fft_max_side"]))
                if reg["iou"] < p["pair_min_iou"]:
                    continue
                de = float(np.linalg.norm(np.asarray(ia.mean_lab) - np.asarray(ib.mean_lab)))
                if de > p["pair_max_delta_e"]:
                    continue
                rigid_iou = rigid[i] / max(1.0, ia.area + ib.area - rigid[i])
                candidates.append((reg["iou"], a, b, de, {**reg, "rigid_iou": float(rigid_iou)}))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for iou, a, b, de, reg in candidates:
        if a in used_a or b in used_b:
            continue
        used_a.add(a)
        used_b.add(b)
        pairs.append({"a": a, "b": b, "iou": round(float(iou), 4), "dx": float(reg["dx"]), "dy": float(reg["dy"]),
                      "scale": round(float(reg["scale"]), 3), "rigid_iou": round(float(reg["rigid_iou"]), 4),
                      "delta_e": round(de, 2)})
    twin = {}
    for pr in pairs:
        twin[pr["a"]] = pr["b"]
        twin[pr["b"]] = pr["a"]
    return {"sides": sides, "pairs": pairs, "twin": twin}


# ----------------------------------------------------------------------------- fold pairing
def fold_overlaps(label_map: np.ndarray, mirror_map: np.ndarray, weights: np.ndarray | None = None
                  ) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], float], dict[int, int]]:
    """For every (r, s): pixels of region r whose twin lies in region s, the sum of `weights` over them, and
    the folded area of every region s (pixels whose twin lies in s)."""
    a = label_map.ravel().astype(np.int64)
    b = mirror_map.ravel().astype(np.int64)
    m = (a > 0) & (b > 0)
    k = int(max(a.max(initial=0), b.max(initial=0))) + 1
    key = a[m] * k + b[m]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=weights.ravel()[m], minlength=len(uk)) if weights is not None else cnt
    counts = {(int(q // k), int(q % k)): int(c) for q, c in zip(uk.tolist(), cnt.tolist())}
    wsum = {(int(q // k), int(q % k)): float(s) for q, s in zip(uk.tolist(), np.asarray(sums).tolist())}
    ids, mcnt = np.unique(b[b > 0], return_counts=True)
    return counts, wsum, {int(i): int(c) for i, c in zip(ids.tolist(), mcnt.tolist())}


def pair_regions_fold(label_map: np.ndarray, mirror_map: np.ndarray, lab_image: np.ndarray,
                      lab_mirror: np.ndarray, regions: dict[int, Any], axis_x_at, band_half_width: float,
                      params: dict | None = None) -> dict[str, Any]:
    """Sides and twins of the regions of one level using the fold.

    mirror_map[p] is the region of p's twin (Fold.mirror(label_map)); lab_mirror the Lab colour at p's twin;
    axis_x_at(y) the local centre line. Returns the same structure as pair_regions, with extra per-pair fields
    fold_iou (before the residual shift), delta_e (mean colours), pattern_de (per-pixel over the overlap), score.
    """
    from scipy.optimize import linear_sum_assignment

    p = {**DEFAULTS, **(params or {})}
    pix_de = np.linalg.norm(lab_image.astype(np.float32) - lab_mirror.astype(np.float32), axis=-1)
    counts, de_sum, marea = fold_overlaps(label_map, mirror_map, pix_de)
    area = {rid: int(info.area) for rid, info in regions.items()}

    # sides: central = folds onto itself and touches the centre band; else by centroid against the local line
    sides: dict[int, str] = {}
    self_iou: dict[int, float] = {}
    for rid, info in regions.items():
        o = counts.get((rid, rid), 0)
        self_iou[rid] = o / max(1, area[rid] + marea.get(rid, 0) - o)
        cx, cy = info.centroid
        ax_here = float(axis_x_at(cy))
        x0, _y0, bw, _bh = info.bbox
        touches_band = x0 <= ax_here + band_half_width and x0 + bw >= ax_here - band_half_width
        if self_iou[rid] >= float(p["axis_self_iou"]) and touches_band:
            sides[rid] = "axis"
        else:
            sides[rid] = "L" if cx < ax_here else "R"

    # candidates: L regions against the R regions their pixels' twins fall in (and vice versa)
    by_left: dict[int, dict[int, int]] = {}
    for (r, s), c in counts.items():
        if r == s:
            continue
        if sides.get(r) == "L" and sides.get(s) == "R":
            by_left.setdefault(r, {})[s] = by_left.get(r, {}).get(s, 0) + c
        elif sides.get(r) == "R" and sides.get(s) == "L":
            by_left.setdefault(s, {})[r] = by_left.get(s, {}).get(r, 0) + c
    shift = int(p["fold_shift_px"])
    h, w = label_map.shape
    # bounding boxes of the folded regions, so the residual-shift window holds both shapes whole
    from scipy import ndimage as ndi
    mids = np.unique(mirror_map)
    mids = mids[mids > 0]
    dense = np.where(mirror_map > 0, np.searchsorted(mids, mirror_map) + 1, 0).astype(np.int32)
    mbox = {int(v): sl for v, sl in zip(mids.tolist(), ndi.find_objects(dense, max_label=len(mids)))}
    cands: list[tuple[float, int, int, dict]] = []
    for a, opts in by_left.items():
        ia = regions[a]
        for b, _c in sorted(opts.items(), key=lambda kv: -kv[1])[: int(p["fold_candidates"])]:
            ib = regions[b]
            ratio = max(area[a], area[b]) / max(1, min(area[a], area[b]))
            if ratio > float(p["pair_area_ratio_max"]):
                continue
            oab, oba = counts.get((a, b), 0), counts.get((b, a), 0)
            union = (area[a] + marea.get(b, 0) - oab) + (area[b] + marea.get(a, 0) - oba)
            fold_iou = (oab + oba) / max(1, union)
            # residual shift: the folded twin against the region, in a window around the region
            x0, y0, bw, bh = ia.bbox
            x1, y1 = x0 + bw, y0 + bh
            sl = mbox.get(b)
            if sl is not None:
                x0, x1 = min(x0, sl[1].start), max(x1, sl[1].stop)
                y0, y1 = min(y0, sl[0].start), max(y1, sl[0].stop)
            g = shift + 2
            xa, ya = max(0, x0 - g), max(0, y0 - g)
            xb, yb = min(w, x1 + g), min(h, y1 + g)
            ma = label_map[ya:yb, xa:xb] == a
            mb = mirror_map[ya:yb, xa:xb] == b
            iou_s, dy, dx = register_masks(mb, ma, shift, int(p["pair_fft_max_side"]))
            iou = max(iou_s, oab / max(1, area[a] + marea.get(b, 0) - oab))
            if iou_s < iou:
                dx = dy = 0
            de = float(np.linalg.norm(np.asarray(ia.mean_lab) - np.asarray(ib.mean_lab)))
            pat = (de_sum.get((a, b), 0.0) + de_sum.get((b, a), 0.0)) / max(1, oab + oba)
            if iou < float(p["pair_min_iou"]) or de > float(p["pair_max_delta_e"]):
                continue
            score = iou * np.exp(-(de / float(p["fold_colour_sigma"])) ** 2) \
                * np.exp(-(pat / float(p["fold_pattern_sigma"])) ** 2)
            cands.append((float(score), a, b, {"iou": float(iou), "fold_iou": float(fold_iou), "dx": int(dx),
                                               "dy": int(dy), "delta_e": de, "pattern_de": float(pat)}))
    pairs = []
    if cands:
        lefts = sorted({c[1] for c in cands})
        rights = sorted({c[2] for c in cands})
        li = {r: i for i, r in enumerate(lefts)}
        ri = {r: i for i, r in enumerate(rights)}
        cost = np.full((len(lefts), len(rights)), 1e6)
        info = {}
        for score, a, b, d in cands:
            cost[li[a], ri[b]] = -score
            info[(a, b)] = (score, d)
        rows, cols = linear_sum_assignment(cost)
        for r_, c_ in zip(rows.tolist(), cols.tolist()):
            if cost[r_, c_] >= 1e6:
                continue
            a, b = lefts[r_], rights[c_]
            score, d = info[(a, b)]
            pairs.append({"a": a, "b": b, "iou": round(d["iou"], 4), "fold_iou": round(d["fold_iou"], 4),
                          "rigid_iou": round(d["fold_iou"], 4), "dx": float(d["dx"]), "dy": float(d["dy"]),
                          "scale": 1.0, "delta_e": round(d["delta_e"], 2), "pattern_de": round(d["pattern_de"], 2),
                          "score": round(score, 4)})
    pairs.sort(key=lambda q: -q["score"])
    twin = {}
    for pr in pairs:
        twin[pr["a"]] = pr["b"]
        twin[pr["b"]] = pr["a"]
    return {"sides": sides, "pairs": pairs, "twin": twin, "self_iou": {k: round(v, 4) for k, v in self_iou.items()},
            "method": "fold"}
