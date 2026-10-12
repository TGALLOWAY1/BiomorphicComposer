"""Automated checks 1-8 (docs/ARCHITECTURE.md, section Verify; docs/V1_BRIEF.md, Verification).

Every public check returns a `CheckResult` and never raises: a check that cannot run (a module that still
raises NotImplementedError, a missing file, a crash) comes back with `passed=None` and a
"not run: <reason>" summary, with the traceback in `details["traceback"]`.

Thresholds are the starting points from the brief. Each check puts what it measured under
`details["measured"]` and the rule it applied under `details["threshold"]`, so the report can quote
"the drawing disagrees: measured X against threshold Y" instead of anybody tuning the numbers to pass.

Convention: keys in `details` that start with an underscore hold live Python objects (masks, tables) for
the contact sheet and are dropped when the result is serialised.
"""
from __future__ import annotations

import functools
import inspect
import sqlite3
import traceback
from dataclasses import dataclass, field, is_dataclass, asdict
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

# ----------------------------------------------------------------------------- thresholds (brief)
GRANULARITY_TARGET = {"total_min": 50, "total_max": 75, "axis_target": 12, "side_max": 29}  # side < 30
INK_MIN_FRACTION = 0.90
AXIS_MIDDLE_FRACTION = 0.30          # axis x must sit in the middle 30 % of the figure box
PAIRING_MIN_FRACTION = 0.80
GROUND_TRUTH_MIN_FRACTION = 0.50     # "a clear majority" as a starting point

DEMO_PALETTE = [  # Lab; used only to exercise the `remap` render mode in check 7
    {"from": [45.0, 10.0, -40.0], "to": [55.0, 60.0, 40.0]},     # navy -> warm red
    {"from": [60.0, -35.0, -5.0], "to": [70.0, 20.0, 60.0]},     # teal -> yellow
    {"from": [85.0, 2.0, 12.0], "to": [85.0, 2.0, 12.0]},        # paper -> paper
    {"from": [20.0, 0.0, 0.0], "to": [20.0, 0.0, 0.0]},          # ink -> ink
]

CHECK_NAMES = (
    "partition_integrity", "ink_fidelity", "axis_check", "granularity",
    "pairing_check", "ground_truth", "non_destructive", "provenance_immutable",
)


# ----------------------------------------------------------------------------- result type
@dataclass
class CheckResult:
    name: str
    passed: bool | None          # None = not run
    summary: str
    details: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return {True: "PASS", False: "FAIL"}.get(self.passed, "NOT RUN")

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "status": self.status,
            "summary": self.summary,
            "details": jsonable({k: v for k, v in self.details.items() if not str(k).startswith("_")}),
        }


def jsonable(obj: Any) -> Any:
    """Recursively convert numpy scalars/arrays, paths, tuples, sets and dataclasses into JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, np.ndarray):
        if obj.size > 64:
            return {"__array__": list(obj.shape), "dtype": str(obj.dtype)}
        return jsonable(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(asdict(obj))
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


def not_run(name: str, reason: BaseException | str, details: dict | None = None) -> CheckResult:
    d = dict(details or {})
    if isinstance(reason, BaseException):
        text = f"{type(reason).__name__}: {reason}"
        d["traceback"] = traceback.format_exc()
    else:
        text = str(reason)
    d["reason"] = text
    return CheckResult(name, None, f"not run: {text}", d)


def _guarded(name: str) -> Callable:
    """Decorator: any exception inside a check becomes a 'not run' result instead of a crash."""
    def deco(fn: Callable[..., CheckResult]) -> Callable[..., CheckResult]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> CheckResult:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the whole point is to degrade, not crash
                return not_run(name, exc)
        return wrapper
    return deco


# ----------------------------------------------------------------------------- shared helpers
def internal_boundaries(label_map: np.ndarray) -> np.ndarray:
    """Pixels whose right or lower neighbour carries a different *nonzero* label; both sides are marked.

    This is the 2-px boundary between neighbouring regions; the figure outline (label vs 0) is excluded.
    """
    b = np.zeros(label_map.shape, bool)
    h = (label_map[:, :-1] != label_map[:, 1:]) & (label_map[:, :-1] > 0) & (label_map[:, 1:] > 0)
    b[:, :-1] |= h
    b[:, 1:] |= h
    v = (label_map[:-1, :] != label_map[1:, :]) & (label_map[:-1, :] > 0) & (label_map[1:, :] > 0)
    b[:-1, :] |= v
    b[1:, :] |= v
    return b


def all_boundaries(label_map: np.ndarray) -> np.ndarray:
    """Like `internal_boundaries` but also marks the figure outline (region against background), 2 px wide."""
    b = np.zeros(label_map.shape, bool)
    h = (label_map[:, :-1] != label_map[:, 1:]) & ((label_map[:, :-1] > 0) | (label_map[:, 1:] > 0))
    b[:, :-1] |= h
    b[:, 1:] |= h
    v = (label_map[:-1, :] != label_map[1:, :]) & ((label_map[:-1, :] > 0) | (label_map[1:, :] > 0))
    b[:-1, :] |= v
    b[1:, :] |= v
    return b


def reflect_mask(mask: np.ndarray, axis_x: float) -> np.ndarray:
    """Mirror a mask/label map about the vertical line x = axis_x (same convention as pipeline.pairing)."""
    h, w = mask.shape[:2]
    xs = np.arange(w)
    xr = np.floor(2.0 * axis_x - xs - 0.5).astype(np.int64)
    valid = (xr >= 0) & (xr < w)
    out = np.zeros_like(mask)
    out[:, valid] = mask[:, xr[valid]]
    return out


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = int(np.count_nonzero(a & b))
    union = int(np.count_nonzero(a | b))
    return inter / union if union else 0.0


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of the true pixels of a mask, or None when empty."""
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)


def band_distance(total: int, n_axis: int, n_left: int, n_right: int,
                  target: dict | None = None) -> float:
    """Distance of a level's counts from the brief's target band (0 = inside the band with 12 on-axis).

    d_total: 0 inside [50, 75], else the shortfall/excess in units of the band width (25).
    d_side:  0 when both sides are < 30, else the excess in units of 29.
    d_axis:  |n_axis - 12| / 12.
    The two hard constraints weigh twice the soft on-axis target: 2*d_total + 2*d_side + d_axis.
    """
    t = {**GRANULARITY_TARGET, **(target or {})}
    lo, hi = t["total_min"], t["total_max"]
    if total < lo:
        d_total = (lo - total) / (hi - lo)
    elif total > hi:
        d_total = (total - hi) / (hi - lo)
    else:
        d_total = 0.0
    d_side = max(0, max(n_left, n_right) - t["side_max"]) / t["side_max"]
    d_axis = abs(n_axis - t["axis_target"]) / t["axis_target"]
    return float(2.0 * d_total + 2.0 * d_side + d_axis)


def level_table(session: Any) -> list[dict]:
    """Per-level counts: regions, on-axis / left / right, pairs, paired fraction, band distance."""
    rows = []
    t = GRANULARITY_TARGET
    for lv in session.levels:
        i = int(lv["index"])
        regions = session.regions(i)
        pairing = session.pairing(i)
        sides = pairing.get("sides", {})
        twin = pairing.get("twin", {})
        n_axis = sum(1 for s in sides.values() if s == "axis")
        n_left = sum(1 for s in sides.values() if s == "L")
        n_right = sum(1 for s in sides.values() if s == "R")
        total = len(regions)
        off_axis = [rid for rid in regions if sides.get(rid) != "axis"]
        paired = sum(1 for rid in off_axis if twin.get(rid) is not None)
        in_band = (t["total_min"] <= total <= t["total_max"]) and max(n_left, n_right) <= t["side_max"]
        rows.append({
            "index": i,
            "n_merges": int(lv.get("n_merges", 0)),
            "cost": lv.get("cost"),
            "n_regions": total,
            "n_axis": n_axis,
            "n_left": n_left,
            "n_right": n_right,
            "n_pairs": len(pairing.get("pairs", [])),
            "n_off_axis": len(off_axis),
            "paired_fraction": (paired / len(off_axis)) if off_axis else None,
            "in_band": bool(in_band),
            "distance": band_distance(total, n_axis, n_left, n_right),
        })
    return rows


def pick_level(session: Any, table: list[dict] | None = None) -> tuple[int, list[dict]]:
    """Pick the level that best satisfies the brief's granularity band.

    Levels inside the hard band (50-75 regions, < 30 per side) are preferred; among them (or among all
    levels when none is in band) the smallest `band_distance` wins, ties going to the finer level.
    """
    table = table if table is not None else level_table(session)
    if not table:
        raise ValueError("session has no levels")
    pool = [r for r in table if r["in_band"]] or table
    best = min(pool, key=lambda r: (r["distance"], r["index"]))
    return int(best["index"]), table


# ----------------------------------------------------------------------------- 1. partition integrity
@_guarded("partition_integrity")
def partition_integrity(session: Any) -> CheckResult:
    """At every level: label_map > 0 equals the figure mask, and re-compositing every tile (cut_tile per
    region, pasted at its bbox where alpha > 0) reproduces the working copy pixel-exactly inside the mask,
    painting every figure pixel exactly once (no gaps, no overlaps)."""
    fig = session.figure_mask
    rgb = session.working_rgb
    rows = []
    worst_mismatch = 0
    worst_diff = 0
    for lv in session.levels:
        i = int(lv["index"])
        lab = session.label_map(i)
        covered = lab > 0
        mismatch = int(np.count_nonzero(covered != fig))
        regions = session.regions(i)
        canvas = np.zeros_like(rgb)
        count = np.zeros(lab.shape, np.uint16)
        for rid in regions:
            rgba, (x, y, w, h) = session.cut_tile(i, rid)
            alpha = rgba[..., 3] > 0
            sub = canvas[y:y + h, x:x + w]
            sub[alpha] = rgba[..., :3][alpha]
            count[y:y + h, x:x + w] += alpha.astype(np.uint16)
        gaps = int(np.count_nonzero((count == 0) & fig))
        overlaps = int(np.count_nonzero(count > 1))
        outside = int(np.count_nonzero((count > 0) & ~fig))
        diff = int(np.count_nonzero(np.any(canvas != rgb, axis=2) & fig))
        cover_ok = mismatch == 0
        recomposite_ok = diff == 0 and gaps == 0 and overlaps == 0 and outside == 0
        worst_mismatch = max(worst_mismatch, mismatch)
        worst_diff = max(worst_diff, diff, gaps, overlaps, outside)
        rows.append({"index": i, "n_regions": len(regions), "cover_ok": cover_ok, "cover_mismatch_px": mismatch,
                     "recomposite_ok": recomposite_ok, "recomposite_diff_px": diff, "gap_px": gaps,
                     "overlap_px": overlaps, "outside_px": outside})
    all_cover = all(r["cover_ok"] for r in rows)
    all_recomp = all(r["recomposite_ok"] for r in rows)
    passed = all_cover and all_recomp and bool(rows)
    bad = [r["index"] for r in rows if not (r["cover_ok"] and r["recomposite_ok"])]
    summary = (f"{len(rows)} levels: label map == figure mask at every level, tiles re-composite pixel-exactly"
               if passed else
               f"{len(rows)} levels; failing levels {bad}; worst cover mismatch {worst_mismatch} px, "
               f"worst re-composite diff/gap/overlap {worst_diff} px")
    return CheckResult("partition_integrity", passed, summary, {
        "levels": rows, "all_cover_ok": all_cover, "all_recomposite_ok": all_recomp,
        "worst_mismatch_px": worst_mismatch, "worst_recomposite_px": worst_diff, "failing_levels": bad,
        "threshold": {"cover_mismatch_px": 0, "recomposite_diff_px": 0, "gap_px": 0, "overlap_px": 0},
        "measured": {"worst_cover_mismatch_px": worst_mismatch, "worst_recomposite_px": worst_diff},
    })


# ----------------------------------------------------------------------------- 2. ink fidelity
@_guarded("ink_fidelity")
def ink_fidelity(session: Any, dilate_px: int = 1, level: int | None = None,
                 min_fraction: float = INK_MIN_FRACTION) -> CheckResult:
    """Per level: fraction of internal region-boundary pixels (both sides of every label change between two
    nonzero labels) that fall on the ink mask dilated by `dilate_px`. Pass if >= 0.90 at the picked level."""
    if level is None:
        level, _ = pick_level(session)
    ink = session.ink_mask
    if dilate_px > 0:
        k = np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), np.uint8)
        ink_d = cv2.dilate(ink.astype(np.uint8), k) > 0
    else:
        ink_d = ink
    rows = []
    for lv in session.levels:
        i = int(lv["index"])
        b = internal_boundaries(session.label_map(i))
        n = int(np.count_nonzero(b))
        on = int(np.count_nonzero(b & ink_d))
        rows.append({"index": i, "n_boundary_px": n, "on_ink_px": on, "off_ink_px": n - on,
                     "fraction": (on / n) if n else None})
    by_index = {r["index"]: r for r in rows}
    picked = by_index[int(level)]
    frac = picked["fraction"]
    vacuous = frac is None
    passed = True if vacuous else frac >= min_fraction
    fracs = [r["fraction"] for r in rows if r["fraction"] is not None]
    if vacuous:
        summary = (f"level {level}: no internal region boundaries (regions are disjoint islands); "
                   f"nothing to measure, counted as pass")
    else:
        summary = (f"level {level}: {frac:.1%} of {picked['n_boundary_px']} boundary px on ink "
                   f"(dilated {dilate_px} px); threshold {min_fraction:.0%}; "
                   f"over all levels min {min(fracs):.1%} / max {max(fracs):.1%}")
    return CheckResult("ink_fidelity", passed, summary, {
        "dilate_px": dilate_px, "picked_level": int(level), "levels": rows, "vacuous": vacuous,
        "threshold": {"min_fraction_at_picked_level": min_fraction},
        "measured": {"fraction_at_picked_level": frac,
                     "min_fraction_over_levels": min(fracs) if fracs else None,
                     "max_fraction_over_levels": max(fracs) if fracs else None,
                     "off_ink_px_at_picked_level": picked["off_ink_px"]},
    })


# ----------------------------------------------------------------------------- 3. axis
@_guarded("axis_check")
def axis_check(session: Any, middle_fraction: float = AXIS_MIDDLE_FRACTION) -> CheckResult:
    """Axis x within the middle 30 % of the figure box (measured on the figure mask in working px);
    reports the pipeline's axis.score and axis.mirror_iou and an independently recomputed mirror IoU."""
    axis = dict(session.meta.get("axis", {}))
    axis_x = float(axis.get("x", session.axis_x))
    fig = session.figure_mask
    box = mask_bbox(fig)
    if box is None:
        raise ValueError("figure mask is empty")
    x, y, w, h = box
    centre_x = x + w / 2.0
    offset = axis_x - centre_x
    offset_frac = offset / w if w else 0.0
    max_off = middle_fraction / 2.0
    passed = abs(offset_frac) <= max_off
    recomputed_iou = mask_iou(fig, reflect_mask(fig, axis_x))
    score = axis.get("score")
    mirror_iou = axis.get("mirror_iou")
    summary = (f"axis x={axis_x:.1f} is {offset:+.1f} px ({offset_frac:+.1%} of width {w}) from the figure "
               f"centre {centre_x:.1f}; allowed +/-{max_off:.0%}; score={_fmt(score)} "
               f"mirror_iou={_fmt(mirror_iou)} (recomputed {recomputed_iou:.3f})")
    return CheckResult("axis_check", passed, summary, {
        "axis_x": axis_x, "angle_deg": axis.get("angle_deg"), "score": score, "mirror_iou": mirror_iou,
        "recomputed_mirror_iou": recomputed_iou, "search": axis.get("search"),
        "figure_box_working": {"x": x, "y": y, "w": w, "h": h}, "figure_centre_x": centre_x,
        "offset_px": offset, "offset_fraction": offset_frac,
        "threshold": {"max_abs_offset_fraction": max_off, "middle_fraction": middle_fraction},
        "measured": {"abs_offset_fraction": abs(offset_frac), "score": score, "mirror_iou": mirror_iou,
                     "recomputed_mirror_iou": recomputed_iou},
    })


def _fmt(v: Any, nd: int = 3) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


# ----------------------------------------------------------------------------- 4. granularity
@_guarded("granularity")
def granularity(session: Any) -> CheckResult:
    """Per level: n_regions, on-axis, left, right. Picks the level closest to the target band
    (see `pick_level`); passes if some level has 50-75 regions AND < 30 per side."""
    picked, table = pick_level(session)
    t = GRANULARITY_TARGET
    in_band = [r["index"] for r in table if r["in_band"]]
    passed = bool(in_band)
    row = next(r for r in table if r["index"] == picked)
    counts = f"{row['n_regions']} regions ({row['n_axis']} on-axis, {row['n_left']} L, {row['n_right']} R)"
    if passed:
        summary = f"picked level {picked}: {counts}; levels in band {in_band}"
    else:
        totals = [r["n_regions"] for r in table]
        summary = (f"no level has {t['total_min']}-{t['total_max']} regions with < {t['side_max'] + 1} per side; "
                   f"closest is level {picked}: {counts}; region counts over levels {totals}")
    return CheckResult("granularity", passed, summary, {
        "picked_level": picked, "in_band_levels": in_band, "levels": table,
        "threshold": {**t, "rule": "some level with total_min <= n_regions <= total_max and max(L, R) <= side_max; "
                                   "pick = min band_distance, in-band levels first, ties to the finer level"},
        "measured": {"picked": row, "n_regions_per_level": {str(r["index"]): r["n_regions"] for r in table}},
    })


# ----------------------------------------------------------------------------- 5. pairing
@_guarded("pairing_check")
def pairing_check(session: Any, level: int, min_fraction: float = PAIRING_MIN_FRACTION) -> CheckResult:
    """Fraction of off-axis regions at `level` that have a mirror twin; pass if >= 0.80."""
    level = int(level)
    pairing = session.pairing(level)
    regions = session.regions(level)
    sides = pairing.get("sides", {})
    twin = pairing.get("twin", {})
    off_axis = [rid for rid in regions if sides.get(rid) != "axis"]
    paired = [rid for rid in off_axis if twin.get(rid) is not None]
    unpaired = [{"id": int(rid), "side": sides.get(rid), "area": int(regions[rid].area),
                 "bbox": list(regions[rid].bbox)} for rid in off_axis if twin.get(rid) is None]
    unpaired.sort(key=lambda u: -u["area"])
    frac = (len(paired) / len(off_axis)) if off_axis else None
    passed = True if frac is None else frac >= min_fraction
    pairs = sorted(pairing.get("pairs", []), key=lambda p: -float(p.get("iou", 0)))
    if frac is None:
        summary = f"level {level}: no off-axis regions, nothing to pair (counted as pass)"
    else:
        summary = (f"level {level}: {len(paired)}/{len(off_axis)} off-axis regions have a twin "
                   f"({frac:.1%}; threshold {min_fraction:.0%}); {len(unpaired)} unpaired")
    return CheckResult("pairing_check", passed, summary, {
        "level": level, "n_regions": len(regions), "n_off_axis": len(off_axis), "n_paired": len(paired),
        "n_axis": sum(1 for s in sides.values() if s == "axis"), "fraction": frac, "unpaired": unpaired,
        "pairs": [jsonable(p) for p in pairs],
        "threshold": {"min_paired_fraction": min_fraction},
        "measured": {"paired_fraction": frac, "n_unpaired": len(unpaired)},
    })


# ----------------------------------------------------------------------------- 6. ground truth
def _row_fields(row: Any) -> tuple[Any, Any, Any, Any]:
    """(label, tile_id, score, matched) from a MatchTable row, whatever its shape."""
    if isinstance(row, dict):
        return row.get("label"), row.get("tile_id"), row.get("score"), row.get("matched")
    if isinstance(row, (tuple, list)):
        vals = list(row) + [None] * 4
        return vals[0], vals[1], vals[2], vals[3]
    return (getattr(row, "label", None), getattr(row, "tile_id", None),
            getattr(row, "score", None), getattr(row, "matched", None))


def _opt(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _tile_id_value(v: Any) -> Any:
    if v is None:
        return None
    return int(v) if isinstance(v, (int, np.integer)) else v


def match_rows(table: Any) -> list[dict]:
    """Normalise a MatchTable (object with .rows, dict with 'rows', or an iterable of rows) to dicts.

    The four contract columns (label, tile_id, score, matched) are always present; the optional extras the
    ground-truth module reports (piece_index, colour, combined, one-to-one assignment, transform) ride along."""
    rows = getattr(table, "rows", None)
    if rows is None and isinstance(table, dict):
        rows = table.get("rows")
    if rows is None:
        rows = list(table)
    out = []
    for r in rows:
        label, tile_id, score, matched = _row_fields(r)
        d = {"label": None if label is None else str(label),
             "tile_id": _tile_id_value(tile_id),
             "score": None if score is None else float(score),
             "matched": None if matched is None else bool(matched)}
        for extra in ("piece_index", "colour", "combined", "assigned_tile_id", "assigned_score", "assigned_matched"):
            v = _opt(r, extra)
            if v is not None:
                d[extra] = _tile_id_value(v) if extra in ("piece_index", "assigned_tile_id") else (
                    bool(v) if extra == "assigned_matched" else float(v))
        tr = _opt(r, "transform")
        if tr is not None:
            desc = getattr(tr, "describe", None)
            d["transform"] = desc() if callable(desc) else str(tr)
        out.append(d)
    return out


def read_rgb(path: Path | str) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"cannot read image {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


@_guarded("ground_truth")
def ground_truth(session: Any, level: int, map_png: Path | str,
                 labels_path: Path | str | None = None,
                 min_fraction: float = GROUND_TRUTH_MIN_FRACTION) -> CheckResult:
    """Segment the hand-made map, label the pieces, match each piece to the best shape-matching region at
    `level` (candidates = every region, mask = label_map == id); pass if the matched fraction >= 0.5."""
    from biomorphic.verify import groundtruth as gt

    level = int(level)
    map_png = Path(map_png)
    map_rgb = read_rgb(map_png)
    pieces = list(gt.segment_map(map_rgb))
    labels_loaded = False
    labels_note = None
    assignment: dict[str, Any] = {}
    if labels_path is not None and Path(labels_path).exists():
        labels = gt.load_labels(Path(labels_path))
        res = gt.assign_labels(pieces, labels)
        if isinstance(res, list) and res and not isinstance(res[0], (dict, str)):
            pieces = list(res)          # a module that returns relabelled pieces
        # otherwise the pieces were labelled in place and `res` describes the assignment
        assignment = {
            "n_assigned": len(getattr(res, "assigned", {}) or {}),
            "unmatched_labels": [e.get("label") for e in (getattr(res, "unmatched", []) or []) if isinstance(e, dict)],
            "unlabelled_pieces": list(getattr(res, "unlabelled", []) or []),
        }
        labels_loaded = True
    else:
        labels_note = f"labels file not found: {labels_path}" if labels_path else "no labels file given"
    lab = session.label_map(level)
    regions = session.regions(level)
    candidates = [(int(rid), lab == rid, tuple(float(v) for v in info.mean_lab)) for rid, info in regions.items()]
    table = gt.match_pieces(pieces, candidates)
    rows = match_rows(table)
    matched = [r for r in rows if r["matched"]]
    misses = [r for r in rows if not r["matched"]]
    frac = (len(matched) / len(rows)) if rows else None
    passed = False if frac is None else frac >= min_fraction
    scores = [r["score"] for r in rows if r["score"] is not None]
    one_to_one = [r for r in rows if r.get("assigned_matched")]
    one_to_one_frac = (len(one_to_one) / len(rows)) if rows and any("assigned_matched" in r for r in rows) else None
    match_threshold = getattr(table, "threshold", None)
    if frac is None:
        summary = f"map {map_png.name}: no pieces/rows to match (pieces={len(pieces)}, candidates={len(candidates)})"
    else:
        summary = (f"map {map_png.name}: {len(matched)}/{len(rows)} pieces matched at level {level} "
                   f"({frac:.0%}; threshold {min_fraction:.0%}"
                   + (f", shape IoU >= {float(match_threshold):.2f}" if match_threshold is not None else "")
                   + (f"; one-to-one {len(one_to_one)}/{len(rows)}" if one_to_one_frac is not None else "")
                   + "); misses: " + (", ".join(str(m["label"]) for m in misses) if misses else "none"))
    return CheckResult("ground_truth", passed, summary, {
        "level": level, "map": str(map_png), "labels_path": None if labels_path is None else str(labels_path),
        "labels_loaded": labels_loaded, "labels_note": labels_note, "label_assignment": assignment,
        "n_pieces": len(pieces), "n_candidates": len(candidates), "n_rows": len(rows),
        "n_matched": len(matched), "matched_fraction": frac, "rows": rows, "misses": misses,
        "one_to_one_matched": len(one_to_one) if one_to_one_frac is not None else None,
        "one_to_one_fraction": one_to_one_frac, "match_threshold": match_threshold,
        "score_min": min(scores) if scores else None, "score_median": float(np.median(scores)) if scores else None,
        "score_max": max(scores) if scores else None, "score_mean": float(np.mean(scores)) if scores else None,
        "threshold": {"min_matched_fraction": min_fraction, "per_piece_match": match_threshold},
        "measured": {"matched_fraction": frac, "n_matched": len(matched), "n_missed": len(misses),
                     "one_to_one_fraction": one_to_one_frac, "score_mean": float(np.mean(scores)) if scores else None},
        "_objects": {"pieces": pieces, "candidates": candidates, "table": table, "map_rgb": map_rgb,
                     "level": level},
    })


# ----------------------------------------------------------------------------- 7. non-destructive renders
def tile_path(library: Any, tile_id: str, library_dir: Path | str | None = None) -> Path:
    """Path of a stored tile PNG: `library.tile_path(id)` when the API has it, else the documented layout."""
    fn = getattr(library, "tile_path", None)
    if callable(fn):
        p = Path(fn(tile_id))
        if p.exists():
            return p
    rec = None
    get = getattr(library, "get", None)
    if callable(get):
        try:
            rec = get(tile_id)
        except Exception:  # noqa: BLE001
            rec = None
    png_path = getattr(rec, "png_path", None) if rec is not None else None
    base = Path(library_dir) if library_dir is not None else _library_dir(library)
    if png_path:
        p = Path(png_path)
        if not p.is_absolute() and base is not None:
            p = base / p
        if p.exists():
            return p
    if base is not None:
        return base / "tiles" / f"{tile_id}.png"
    raise FileNotFoundError(f"cannot locate the PNG of tile {tile_id}")


def _library_dir(library: Any) -> Path | None:
    for attr in ("library_dir", "dir", "root", "path"):
        v = getattr(library, attr, None)
        if v is not None and not callable(v):
            p = Path(v)
            return p if p.is_dir() else p.parent
    return None


def _db_path(library: Any, library_dir: Path | str | None) -> Path:
    for attr in ("db_path", "sqlite_path", "database_path"):
        v = getattr(library, attr, None)
        if v is not None and not callable(v):
            return Path(v)
    base = Path(library_dir) if library_dir is not None else _library_dir(library)
    if base is None:
        raise FileNotFoundError("cannot locate library.sqlite")
    return base / "library.sqlite"


def _sha_file(p: Path) -> str:
    from biomorphic.tiles import sha256
    return sha256(p.read_bytes())


@_guarded("non_destructive")
def non_destructive(library: Any, tile_ids: list[str], library_dir: Path | str | None = None,
                    palette: list[dict] | None = None) -> CheckResult:
    """sha256 of every stored PNG (on disk and through the API) before and after running every render mode
    on the decoded tile; pass if identical for all tiles and modes and no file appeared in tiles/."""
    from biomorphic.library import render as render_mod
    from biomorphic.tiles import decode_png, sha256

    tile_ids = [str(t) for t in tile_ids]
    if not tile_ids:
        return not_run("non_destructive", "no tiles were saved to the library, nothing to hash")
    modes = tuple(getattr(render_mod, "MODES", ("original", "greyscale", "outline", "remap")))
    palette = palette or DEMO_PALETTE
    base = Path(library_dir) if library_dir is not None else _library_dir(library)
    tiles_dir = (base / "tiles") if base is not None else None
    files_before = sorted(p.name for p in tiles_dir.iterdir()) if tiles_dir and tiles_dir.exists() else None
    rows = []
    for tid in tile_ids:
        path = tile_path(library, tid, library_dir)
        before = _sha_file(path)
        api_before = sha256(library.tile_png_bytes(tid))
        rgba = decode_png(library.tile_png_bytes(tid))
        mode_rows = {}
        for mode in modes:
            out = render_mod.render(rgba, mode, palette=palette if mode == "remap" else None)
            out = np.asarray(out)
            mode_rows[mode] = {"shape": list(out.shape), "dtype": str(out.dtype),
                               "differs_from_original": bool(out.shape != rgba.shape or not np.array_equal(out, rgba)),
                               "alpha_preserved": bool(out.ndim == 3 and out.shape[2] == 4 and
                                                       np.array_equal(out[..., 3], rgba[..., 3]))}
        after = _sha_file(path)
        api_after = sha256(library.tile_png_bytes(tid))
        rows.append({"id": tid, "path": str(path), "sha_before": before, "sha_after": after,
                     "api_sha_before": api_before, "api_sha_after": api_after,
                     "identical": before == after == api_before == api_after, "modes": mode_rows})
    files_after = sorted(p.name for p in tiles_dir.iterdir()) if tiles_dir and tiles_dir.exists() else None
    no_new_files = files_before == files_after
    changed = [r["id"] for r in rows if not r["identical"]]
    passed = not changed and no_new_files
    summary = (f"{len(rows)} tiles x {len(modes)} render modes: stored PNG sha256 unchanged, no files added"
               if passed else
               f"changed tiles: {changed}; files added in tiles/: {no_new_files is False}")
    return CheckResult("non_destructive", passed, summary, {
        "modes": list(modes), "tiles": rows, "files_before": files_before, "files_after": files_after,
        "no_new_files": no_new_files, "changed": changed,
        "threshold": {"rule": "sha256 identical before/after every render mode, on disk and via tile_png_bytes"},
        "measured": {"n_tiles": len(rows), "n_changed": len(changed), "n_modes": len(modes)},
    })


# ----------------------------------------------------------------------------- 8. provenance immutable
@_guarded("provenance_immutable")
def provenance_immutable(library: Any, library_dir: Path | str | None = None) -> CheckResult:
    """library.provenance_is_immutable() is True, the Library class has no other public member whose name
    contains 'provenance', and a direct UPDATE provenance through sqlite3 raises IntegrityError."""
    cls = type(library)
    api_flag = bool(library.provenance_is_immutable())
    public = sorted(m for m in dir(cls) if not m.startswith("_") and "provenance" in m.lower())
    extra = [m for m in public if m != "provenance_is_immutable"]
    # any public method that takes provenance-looking keyword arguments is suspicious too; report, don't fail
    suspicious = []
    for m in dir(cls):
        if m.startswith("_"):
            continue
        fn = getattr(cls, m, None)
        if not callable(fn):
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        if any("provenance" in p.lower() for p in params) and m != "provenance_is_immutable":
            suspicious.append(m)
    db_path = _db_path(library, library_dir)
    trigger_present = False
    n_rows = 0
    update_raised = False
    update_error = None
    update_outcome = None
    con = sqlite3.connect(str(db_path))
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")]
        trigger_present = any("provenance" in n.lower() for n in names)
        n_rows = int(con.execute("SELECT COUNT(*) FROM provenance").fetchone()[0])
        try:
            con.execute("UPDATE provenance SET session_id = 'tampered-by-verify'")
            update_outcome = "UPDATE statement was accepted (rolled back by verify)"
        except sqlite3.IntegrityError as exc:
            update_raised = True
            update_error = f"{type(exc).__name__}: {exc}"
        except sqlite3.DatabaseError as exc:
            update_error = f"{type(exc).__name__}: {exc}"
            update_outcome = "UPDATE failed with a non-IntegrityError"
        finally:
            con.rollback()
    finally:
        con.close()
    if n_rows == 0:
        return not_run("provenance_immutable",
                       "provenance table is empty, the UPDATE trigger cannot be exercised",
                       {"api_flag": api_flag, "public_provenance_members": public, "extra_members": extra,
                        "trigger_present": trigger_present, "db_path": str(db_path), "n_rows": 0})
    passed = api_flag and not extra and update_raised
    parts = [f"provenance_is_immutable()={api_flag}",
             f"public members mentioning provenance: {public}",
             f"direct UPDATE on {n_rows} rows -> " + (update_error if update_raised else f"NOT blocked ({update_outcome})")]
    return CheckResult("provenance_immutable", passed, "; ".join(parts), {
        "api_flag": api_flag, "public_provenance_members": public, "extra_members": extra,
        "suspicious_methods": suspicious, "trigger_present": trigger_present, "trigger_names": names,
        "n_rows": n_rows, "update_raised_integrity_error": update_raised, "update_error": update_error,
        "update_outcome": update_outcome, "db_path": str(db_path),
        "threshold": {"rule": "api flag True AND no other public 'provenance' member AND UPDATE raises IntegrityError"},
        "measured": {"api_flag": api_flag, "n_extra_members": len(extra), "update_raised": update_raised},
    })
