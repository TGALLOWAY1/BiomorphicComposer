"""Grouping cost: how many gestures the review UI needs to assemble each of the artist's chunks.

The artist's hand-made map (verify check 6) names the components they would harvest: chunks of one to a
handful of ink-enclosed cells. The slider cuts a precomputed merge tree, so a chunk is cheap when some
level shows it as one region and expensive when its cells must be tapped one by one and merged. This
module measures that cost on a session, per chunk, under the UI as built:

1. **Target.** Every map piece is shape-matched against every level (groundtruth.match_map_to_session_levels).
   The matcher is scale-free, which lets a large chunk anchor on a small region of the same silhouette, so
   the one thing the map and the drawing share is put back: a single scale (working px per map px), taken
   as the median ratio over the confident matches. Only regions whose area fits the piece at that scale may
   anchor it; the best-scoring one does, and the matcher's (flip, rotation, shift) between normalised
   frames becomes one affine map from map pixels to working pixels with the scale pinned. The chunk's
   *target* is the set of leaf fragments mostly (>= `min_leaf_fraction` of their area) under the projected
   piece. The projection is then re-fitted on the union of those leaves (scale still pinned) and the target
   recomputed; the best-fitting iteration is kept, so one weak anchor does not fix the answer. Pieces are
   placed most confident first and the leaves they take are not offered as anchors to later pieces; two
   pieces that still land on the same leaves are taken as a twin pair and the second moves to the twin
   leaves of the first (the level-0 pairing). `leaf_iou` is the scale-normalised shape IoU of the union
   against the piece: how well the atomic cells can reproduce the chunk at all (low = the split itself
   disagrees with the artist, no grouping gesture fixes it); `area_ratio` is the union's area over the
   piece's expected area. A placement is *confident* when both are in range; the headline totals use only
   those, the full totals follow.
2. **Cost at one level.** A region is *clean* for a target when all its leaves lie in the target. A level
   can assemble the target when every region touching the target is clean; the cost is then one tap per
   clean region plus one Merge when there is more than one. Level 0 can always assemble it.
3. **Mixed plan.** Levels are visited coarse to fine; at each one the clean regions not yet covered are
   tapped and merged (a group survives finer levels, and only grows into regions inside its own node).
   Because regions nest, this greedy order taps the fewest regions. Its gesture count is taps + merges +
   level changes.

Mirror twins co-select in the UI, so a chunk whose leaves are the twins of another chunk's leaves (by the
level-0 pairing) rides along for free; the report counts those pairs and the total with and without them.

Public API
----------
project_piece(piece_mask, transform, piece_centroid, anchor_centroid, shape, scale_ratio) -> bool mask
target_for_piece(session, piece, level, region_id, transform, ...) -> Target     refined leaf set
level_cost(session, level, leaves) -> LevelCost | None                           None = not assemblable
best_single_level(session, leaves) -> LevelCost ; mixed_plan(session, leaves) -> MixedPlan
global_scale(tables) -> (scale, info) ; twin_leaf_map(session) ; mirror_pairs(session, targets)
grouping_cost(session, map_rgb, labels) -> dict                                  the whole report
render_grouping_sheet(session, result, out_path) -> Path ; write_grouping_report(result, out_dir) -> Path
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from biomorphic.verify import groundtruth as gt
from biomorphic.verify.checks import jsonable, mask_bbox

MIN_LEAF_FRACTION = 0.5    # a leaf belongs to the target when this fraction of its area is under the piece
REFINE_ITERS = 4           # re-fit the projection on the leaf union at most this many times
MIRROR_PAIR_FRACTION = 0.8 # chunks are twins when this fraction of one's leaves fold onto the other's
SCALE_CONFIDENT = 0.8      # matches at this shape IoU or better vote for the global scale
SCALE_BAND = 0.25          # votes within this fraction of the first median are kept for the second
ANCHOR_AREA_RANGE = (0.35, 2.5)   # a region may anchor a piece when its area / expected area lies in here
CONFIDENT_IOU = 0.7               # a placement is trusted from this leaf IoU ...
CONFIDENT_AREA = (0.6, 1.6)       # ... and inside this area-ratio band


# ============================================================================= projection
def _affine_map_to_working(piece_centroid: Sequence[float], transform: gt.ShapeTransform,
                           anchor_centroid: Sequence[float], canvas: int = gt.FRAME_CANVAS,
                           scale_ratio: float | None = None) -> np.ndarray:
    """3x3 matrix taking map pixel coordinates to working pixel coordinates.

    The matcher normalises both masks (centroid at the frame centre, sqrt(area) = FRAME_SIZE), then flips,
    rotates about the frame centre and shifts the piece frame onto the candidate frame. Composing those
    with the two normalisations gives the map -> working map; cv2 conventions throughout (x right, y down,
    positive angle counter-clockwise). With `scale_ratio` (working px per map px) the candidate's
    normalisation scale is replaced so the piece lands at that size instead of the candidate's.
    """
    n = float(canvas)
    o = n / 2 - 0.5
    scale_b = transform.scale_b if scale_ratio is None else transform.scale_a / float(scale_ratio)
    px, py = float(piece_centroid[0]), float(piece_centroid[1])
    ax, ay = float(anchor_centroid[0]), float(anchor_centroid[1])

    def tr(x: float, y: float) -> np.ndarray:
        return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)

    def sc(s: float) -> np.ndarray:
        return np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], np.float64)

    to_frame = tr(n / 2, n / 2) @ sc(transform.scale_a) @ tr(-px, -py)
    flip = np.array([[-1, 0, 2 * o], [0, 1, 0], [0, 0, 1]], np.float64) if transform.mirrored else np.eye(3)
    rot = np.eye(3)
    if transform.angle_deg:
        rot[:2] = cv2.getRotationMatrix2D((o, o), float(transform.angle_deg), 1.0)
    shift = tr(float(transform.dx), float(transform.dy))
    from_frame = tr(ax, ay) @ sc(1.0 / scale_b) @ tr(-n / 2, -n / 2)
    return from_frame @ shift @ rot @ flip @ to_frame


def project_piece(piece_mask: np.ndarray, transform: gt.ShapeTransform, piece_centroid: Sequence[float],
                  anchor_centroid: Sequence[float], shape: tuple[int, int],
                  scale_ratio: float | None = None) -> np.ndarray:
    """Project a map piece mask onto the working copy (HxW bool) through the matcher's transform, anchored
    on the centroid of the region it matched; `scale_ratio` pins the size (working px per map px)."""
    piece_mask = np.asarray(piece_mask).astype(bool)
    bb = mask_bbox(piece_mask)
    if bb is None:
        raise ValueError("empty piece mask")
    x, y, w, h = bb
    m = _affine_map_to_working(piece_centroid, transform, anchor_centroid, scale_ratio=scale_ratio)
    m = m @ np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], np.float64)     # crop coords -> map coords
    crop = piece_mask[y:y + h, x:x + w].astype(np.uint8)
    H, W = int(shape[0]), int(shape[1])
    out = cv2.warpAffine(crop, m[:2].astype(np.float64), (W, H), flags=cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return out > 0


def _leaf_areas(session: Any) -> np.ndarray:
    return np.bincount(session.fragments.ravel(), minlength=session.tree.n_leaves + 1).astype(np.float64)


def leaves_under_mask(session: Any, mask: np.ndarray, min_fraction: float = MIN_LEAF_FRACTION,
                      leaf_areas: np.ndarray | None = None) -> list[int]:
    """Leaf ids with at least `min_fraction` of their area under `mask`."""
    areas = _leaf_areas(session) if leaf_areas is None else leaf_areas
    inside = np.bincount(session.fragments[mask].ravel(), minlength=areas.size).astype(np.float64)
    frac = inside / np.maximum(areas, 1.0)
    ids = np.nonzero(frac >= min_fraction)[0]
    return [int(i) for i in ids if i != 0]


def _union_mask(session: Any, leaves: Sequence[int]) -> np.ndarray:
    keep = np.zeros(session.tree.n_leaves + 1, bool)
    keep[list(leaves)] = True
    return keep[session.fragments]


def _centroid(mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


@dataclass
class Target:
    """The leaf set a map piece corresponds to on the working copy."""

    label: str
    piece_index: int
    leaves: tuple[int, ...]
    leaf_iou: float                       # shape IoU of the leaf union against the piece (mirror allowed)
    anchor_level: int
    anchor_region: int
    anchor_score: float                   # the matcher's IoU for that region
    mirrored: bool                        # the piece matched the drawing flipped (its twin side)
    iterations: int
    settled: bool
    area_ratio: float | None = None       # union area / expected area at the global scale (None: no scale)
    placed_by: str = "anchor"             # "anchor" | "twin of <label>"
    projected: np.ndarray | None = field(default=None, repr=False)

    @property
    def confident(self) -> bool:
        """The placement is trusted when the shape fits and (with a scale) the size is right."""
        if self.leaf_iou < CONFIDENT_IOU:
            return False
        return self.area_ratio is None or CONFIDENT_AREA[0] <= self.area_ratio <= CONFIDENT_AREA[1]

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "piece_index": self.piece_index, "leaves": list(self.leaves),
                "n_leaves": len(self.leaves), "leaf_iou": round(self.leaf_iou, 4),
                "anchor_level": self.anchor_level, "anchor_region": self.anchor_region,
                "anchor_score": round(self.anchor_score, 4), "mirrored": self.mirrored,
                "iterations": self.iterations, "settled": self.settled,
                "area_ratio": None if self.area_ratio is None else round(self.area_ratio, 3),
                "placed_by": self.placed_by, "confident": self.confident}


def _judge(session: Any, piece: Any, leaves: Sequence[int], areas: np.ndarray, expected: float | None
           ) -> tuple[float, float, float | None]:
    """(fit, shape IoU, area ratio) of a leaf set for a piece: the fit discounts the IoU by how far the
    union's size is from the expected one (geometric mean of IoU and the size agreement)."""
    iou, _tr = gt.shape_score(piece.mask, _union_mask(session, leaves))
    if expected is None:
        return float(iou), float(iou), None
    ratio = float(areas[list(leaves)].sum() / max(1.0, expected))
    return float(iou) * min(ratio, 1.0 / ratio) ** 0.5, float(iou), ratio


def target_for_piece(session: Any, piece: Any, level: int, region_id: int, transform: gt.ShapeTransform,
                     *, anchor_score: float = 0.0, scale_ratio: float | None = None,
                     min_fraction: float = MIN_LEAF_FRACTION, iters: int = REFINE_ITERS,
                     leaf_areas: np.ndarray | None = None) -> Target:
    """Leaves of the chunk a map piece names: project, take the leaves under the projection, re-fit on their
    union and repeat; the best-fitting iteration wins. With `scale_ratio` the piece keeps its true size on
    the drawing through every refit."""
    shape = (session.height, session.width)
    areas = _leaf_areas(session) if leaf_areas is None else leaf_areas
    expected = None if scale_ratio is None else float(piece.area) * float(scale_ratio) ** 2
    info = session.regions(int(level))[int(region_id)]
    projected = project_piece(piece.mask, transform, piece.centroid, info.centroid, shape, scale_ratio)
    leaves = leaves_under_mask(session, projected, min_fraction, areas)
    if not leaves:   # the projection missed every leaf: fall back to the anchor region itself
        leaves = [int(i) for i in info.leaves]
    best_leaves = list(leaves)
    best_fit, iou, ratio = _judge(session, piece, leaves, areas, expected)
    settled = False
    n_iter = 0
    for n_iter in range(1, int(iters) + 1):
        union = _union_mask(session, leaves)
        _iou, tr = gt.shape_score(piece.mask, union)
        projected = project_piece(piece.mask, tr, piece.centroid, _centroid(union), shape, scale_ratio)
        new = leaves_under_mask(session, projected, min_fraction, areas) or leaves
        if new == leaves:
            settled = True
            break
        leaves = new
        fit, iou_n, ratio_n = _judge(session, piece, leaves, areas, expected)
        if fit > best_fit:
            best_leaves, best_fit, iou, ratio = list(leaves), fit, iou_n, ratio_n
    return Target(label=piece.name, piece_index=int(piece.index), leaves=tuple(sorted(best_leaves)),
                  leaf_iou=float(iou), anchor_level=int(level), anchor_region=int(region_id),
                  anchor_score=float(anchor_score), mirrored=bool(transform.mirrored),
                  iterations=n_iter, settled=settled, area_ratio=ratio, projected=projected)


# ============================================================================= gesture cost
@dataclass
class LevelCost:
    level: int
    n_regions: int                         # regions at that level
    regions: tuple[int, ...]               # the clean regions to tap
    taps: int
    merges: int

    @property
    def gestures(self) -> int:
        return self.taps + self.merges

    def to_json(self) -> dict[str, Any]:
        return {"level": self.level, "n_regions": self.n_regions, "regions": list(self.regions),
                "taps": self.taps, "merges": self.merges, "gestures": self.gestures}


def _leaf_roots(session: Any, level: int) -> np.ndarray:
    """Region id of every leaf at a level, ignoring manual groups (index 0 unused)."""
    root = session.roots(session.level_n_merges(int(level)))
    return root[: session.tree.n_leaves + 1]


def level_cost(session: Any, level: int, leaves: Sequence[int]) -> LevelCost | None:
    """Taps + merge to assemble `leaves` at one level, or None when some region there straddles the target."""
    target = np.zeros(session.tree.n_leaves + 1, bool)
    target[list(leaves)] = True
    roots = _leaf_roots(session, level)
    touched = np.unique(roots[target])
    for r in touched:
        members = np.nonzero(roots == r)[0]
        if not target[members[members != 0]].all():
            return None
    taps = int(touched.size)
    n_regions = int(session.levels[int(level)]["n_regions"])
    return LevelCost(level=int(level), n_regions=n_regions, regions=tuple(int(r) for r in touched),
                     taps=taps, merges=1 if taps > 1 else 0)


def best_single_level(session: Any, leaves: Sequence[int]) -> LevelCost:
    """Cheapest level that assembles the target in one visit (ties go to the coarser level, fewer regions
    on screen to pick from)."""
    best: LevelCost | None = None
    for lv in range(len(session.levels)):
        c = level_cost(session, lv, leaves)
        if c is None:
            continue
        if best is None or c.gestures < best.gestures or (c.gestures == best.gestures and c.level > best.level):
            best = c
    assert best is not None, "level 0 always assembles a leaf set"
    return best


@dataclass
class MixedPlan:
    steps: list[LevelCost]                 # coarse to fine, each step's regions are tapped then merged
    taps: int
    merges: int
    level_changes: int

    @property
    def gestures(self) -> int:
        return self.taps + self.merges + self.level_changes

    def to_json(self) -> dict[str, Any]:
        return {"steps": [s.to_json() for s in self.steps], "taps": self.taps, "merges": self.merges,
                "level_changes": self.level_changes, "gestures": self.gestures}


def mixed_plan(session: Any, leaves: Sequence[int]) -> MixedPlan:
    """Tap the largest clean regions first (coarse levels), then descend for the rest."""
    n = session.tree.n_leaves + 1
    target = np.zeros(n, bool)
    target[list(leaves)] = True
    covered = np.zeros(n, bool)
    steps: list[LevelCost] = []
    for lv in range(len(session.levels) - 1, -1, -1):
        if covered[target].all():
            break
        roots = _leaf_roots(session, lv)
        picked: list[int] = []
        for r in np.unique(roots[target & ~covered]):
            members = np.nonzero(roots == r)[0]
            members = members[members != 0]
            if target[members].all():
                picked.append(int(r))
                covered[members] = True
        if picked:
            steps.append(LevelCost(level=lv, n_regions=int(session.levels[lv]["n_regions"]),
                                   regions=tuple(picked), taps=len(picked), merges=0))
    taps = sum(s.taps for s in steps)
    # one Merge per visited level once there is more than one region in play (the group from the previous
    # level counts as a region on the next one)
    merges = 0
    in_play = 0
    for s in steps:
        in_play += s.taps
        if in_play > 1:
            s.merges = 1
            merges += 1
            in_play = 1
    return MixedPlan(steps=steps, taps=taps, merges=merges, level_changes=max(0, len(steps) - 1))


# ============================================================================= twins between chunks
def twin_leaf_map(session: Any) -> dict[int, set[int]]:
    """leaf id -> the leaves of its twin region at level 0 (missing when unpaired or without a pairing)."""
    try:
        twin = session.pairing(0)["twin"]
    except Exception:  # noqa: BLE001 - no fold / pairing unavailable: no twins rather than a failure
        return {}
    roots = _leaf_roots(session, 0)
    region_leaves: dict[int, set[int]] = {}
    for leaf in range(1, roots.size):
        region_leaves.setdefault(int(roots[leaf]), set()).add(leaf)
    out: dict[int, set[int]] = {}
    for rid, leaves in region_leaves.items():
        t = twin.get(rid)
        if t is None:
            t = twin.get(str(rid))
        if t is not None:
            tl = region_leaves.get(int(t), set())
            for leaf in leaves:
                out[leaf] = tl
    return out


def mirror_pairs(session: Any, targets: Sequence[Target], min_fraction: float = MIRROR_PAIR_FRACTION
                 ) -> list[tuple[str, str, float]]:
    """(label a, label b, fraction) for chunks whose leaves are each other's twins at level 0."""
    twin_leaves = twin_leaf_map(session)
    if not twin_leaves:
        return []
    by_label = {t.label: set(t.leaves) for t in targets}
    out: list[tuple[str, str, float]] = []
    labels = sorted(by_label)
    for i, a in enumerate(labels):
        la = by_label[a]
        if not la:
            continue
        folded: set[int] = set()
        for leaf in la:
            folded |= twin_leaves.get(leaf, set())
        best, best_f = None, 0.0
        for b in labels[i + 1:]:
            lb = by_label[b]
            if not lb:
                continue
            score = min(len(folded & lb) / max(1, len(lb)), len(folded & lb) / max(1, len(la)))
            if score > best_f:
                best, best_f = b, score
        if best is not None and best_f >= min_fraction:
            out.append((a, best, round(best_f, 3)))
    return out


# ============================================================================= the whole measurement
def global_scale(tables: dict[int, gt.MatchTable], confident: float = SCALE_CONFIDENT,
                 band: float = SCALE_BAND) -> tuple[float | None, dict[str, Any]]:
    """Working px per map px, as the median scale ratio of confident matches over every level, then the
    median of the votes within `band` of it (matches at the wrong scale vote far away and drop out)."""
    votes = [row.transform.scale_a / row.transform.scale_b
             for table in tables.values() for row in table.rows if row.score >= confident]
    if not votes:
        return None, {"votes": 0, "kept": 0}
    first = float(np.median(votes))
    kept = [v for v in votes if abs(v - first) <= band * first]
    return float(np.median(kept)), {"votes": len(votes), "kept": len(kept), "first_median": round(first, 4)}


def _anchor_candidates(session: Any, tables: dict[int, gt.MatchTable], piece: Any, scale: float | None,
                       area_range: tuple[float, float] = ANCHOR_AREA_RANGE) -> list[tuple[float, int, int]]:
    """(shape IoU, level, region id) for every region that may anchor the piece, best first: at the global
    scale only regions of a plausible area; without one, everything."""
    expected = None if scale is None else float(piece.area) * float(scale) ** 2
    out: list[tuple[float, int, int]] = []
    for lv, table in tables.items():
        i = next((k for k, r in enumerate(table.rows) if r.piece_index == piece.index), None)
        if i is None:
            continue
        regs = session.regions(int(lv))
        scores = table.shape_scores[i]
        for j, cid in enumerate(table.candidate_ids):
            rid = int(cid)
            if expected is not None:
                info = regs.get(rid)
                if info is None or not (area_range[0] * expected <= info.area <= area_range[1] * expected):
                    continue
            out.append((float(scores[j]), int(lv), rid))
    out.sort(key=lambda t: (-t[0], t[1]))
    return out


def resolve_duplicates(session: Any, targets: list[Target], pieces: dict[int, Any], areas: np.ndarray,
                       scale: float | None) -> list[Target]:
    """Two chunks on the same leaves are usually a twin pair that both folded onto one side. The later one
    (lower anchor score) is moved to the twin leaves of the first, when the drawing has them and the shape
    still fits about as well."""
    twin_leaves = twin_leaf_map(session)
    taken: dict[tuple[int, ...], Target] = {}
    out: list[Target] = []
    for t in targets:                       # targets arrive most confident first
        key = tuple(t.leaves)
        first = taken.get(key)
        if first is None:
            taken[key] = t
            out.append(t)
            continue
        twins: set[int] = set()
        for leaf in first.leaves:
            twins |= twin_leaves.get(leaf, set())
        twins -= set(first.leaves)
        if twins and tuple(sorted(twins)) not in taken:
            piece = pieces[t.piece_index]
            expected = None if scale is None else float(piece.area) * float(scale) ** 2
            _fit, iou, ratio = _judge(session, piece, sorted(twins), areas, expected)
            if iou >= t.leaf_iou - 0.1:
                t = Target(label=t.label, piece_index=t.piece_index, leaves=tuple(sorted(twins)),
                           leaf_iou=iou, anchor_level=t.anchor_level, anchor_region=t.anchor_region,
                           anchor_score=t.anchor_score, mirrored=not t.mirrored, iterations=0, settled=True,
                           area_ratio=ratio, placed_by=f"twin of {first.label}")
                taken[tuple(t.leaves)] = t
        out.append(t)
    return out


def _row_for_target(session: Any, t: Target, areas: np.ndarray) -> dict[str, Any]:
    single = best_single_level(session, t.leaves)
    mixed = mixed_plan(session, t.leaves)
    per_level = {lv: level_cost(session, lv, t.leaves) for lv in range(len(session.levels))}
    feasible = [lv for lv, c in per_level.items() if c is not None]
    one_region_levels = [lv for lv, c in per_level.items() if c is not None and c.taps == 1]
    return {
        **t.to_json(),
        "area_px": int(areas[list(t.leaves)].sum()) if t.leaves else 0,
        "single": single.to_json(), "mixed": mixed.to_json(),
        "feasible_levels": feasible, "one_region_levels": one_region_levels,
        "one_region": bool(one_region_levels),
        "per_level_taps": {str(lv): (None if c is None else c.taps) for lv, c in per_level.items()},
    }


def _totals(rows: list[dict[str, Any]], paired_second: set[str]) -> dict[str, Any]:
    sizes = [r["n_leaves"] for r in rows]
    sg = [r["single"]["gestures"] for r in rows]
    mx = [r["mixed"]["gestures"] for r in rows]
    return {
        "n": len(rows),
        "chunk_size": {"min": min(sizes) if sizes else None,
                       "median": float(np.median(sizes)) if sizes else None, "max": max(sizes) if sizes else None,
                       "histogram": {str(k): int(v) for k, v in zip(*np.unique(sizes, return_counts=True))}},
        "one_region_pieces": sum(r["one_region"] for r in rows),
        "single_level": {"taps": sum(r["single"]["taps"] for r in rows), "gestures": sum(sg),
                         "median_gestures": float(np.median(sg)) if sg else None, "max_gestures": max(sg, default=None)},
        "mixed": {"gestures": sum(mx), "median_gestures": float(np.median(mx)) if mx else None,
                  "max_gestures": max(mx, default=None),
                  "gestures_with_mirror_coselect": sum(r["mixed"]["gestures"] for r in rows
                                                       if r["label"] not in paired_second)},
    }


def grouping_cost(session: Any, map_rgb: np.ndarray, labels: dict[str, Any] | None = None, *,
                  min_fraction: float = MIN_LEAF_FRACTION, max_seconds: float = 90.0) -> dict[str, Any]:
    """Measure the grouping cost of every map piece on a session. Returns a JSON-able dict (live objects
    under `_objects`, stripped by checks.jsonable)."""
    res = gt.match_map_to_session_levels(map_rgb, session, labels, max_seconds=max_seconds,
                                         must_include=[0])
    pieces: list[Any] = list(res["_pieces"])
    tables: dict[int, gt.MatchTable] = res["_tables"]
    areas = _leaf_areas(session)
    scale, scale_info = global_scale(tables)
    targets: list[Target] = []
    # most confident pieces first; the leaves they take cannot anchor a later piece
    ranked = [(p, _anchor_candidates(session, tables, p, scale)) for p in pieces]
    ranked.sort(key=lambda pc: -(pc[1][0][0] if pc[1] else -1.0))
    claimed = np.zeros(session.tree.n_leaves + 1, bool)
    roots_by_level: dict[int, np.ndarray] = {}
    for p, cands in ranked:
        pick = None
        for score, lv, rid in cands:
            roots = roots_by_level.setdefault(lv, _leaf_roots(session, lv))
            members = np.nonzero(roots == rid)[0]
            if claimed[members[members != 0]].all():
                continue
            pick = (score, lv, rid)
            break
        if pick is None:
            continue
        score, lv, rid = pick
        # the transform of the chosen region: re-fit, the table only kept the best candidate's
        _iou, tr = gt.shape_score(p.mask, session.label_map(lv) == rid)
        t = target_for_piece(session, p, lv, rid, tr, anchor_score=score, scale_ratio=scale,
                             min_fraction=min_fraction, leaf_areas=areas)
        claimed[list(t.leaves)] = True
        targets.append(t)
    targets = resolve_duplicates(session, targets, {p.index: p for p in pieces}, areas, scale)
    rows = [_row_for_target(session, t, areas) for t in targets]
    rows.sort(key=lambda r: (r["label"].startswith("#"), r["label"]))

    pairs = mirror_pairs(session, targets)
    paired_second = {b for _a, b, _f in pairs}
    by_leaves: dict[tuple[int, ...], list[str]] = {}
    for r in rows:
        by_leaves.setdefault(tuple(r["leaves"]), []).append(r["label"])
    summary = {
        "n_pieces": len(rows), "n_leaves_total": int(session.tree.n_leaves),
        "scale": None if scale is None else round(scale, 4), "scale_info": scale_info,
        "confident": _totals([r for r in rows if r["confident"]], paired_second),
        "all": _totals(rows, paired_second),
        "uncertain": [r["label"] for r in rows if not r["confident"]],
        "confident_rule": {"leaf_iou": CONFIDENT_IOU, "area_ratio": list(CONFIDENT_AREA)},
        "duplicate_targets": [labs for labs in by_leaves.values() if len(labs) > 1],
        "unplaced": [p.name for p in pieces if p.name not in {t.label for t in targets}],
        "placed_by_twin": [r["label"] for r in rows if r["placed_by"] != "anchor"],
        "mirror_pairs": [{"a": a, "b": b, "fraction": f} for a, b, f in pairs],
        "low_leaf_iou": [r["label"] for r in rows if r["leaf_iou"] < gt.DEFAULT_THRESHOLD],
        "leaf_iou_threshold": gt.DEFAULT_THRESHOLD,
        "levels": [{"index": int(l["index"]), "n_regions": int(l["n_regions"])} for l in session.levels],
    }
    return {"summary": summary, "rows": rows, "session_id": session.session_id,
            "source": session.meta.get("source", {}).get("filename", ""),
            "_objects": {"pieces": pieces, "targets": targets, "map_rgb": map_rgb}}


# ============================================================================= outputs
def _palette(n: int) -> list[tuple[int, int, int]]:
    import colorsys
    return [tuple(int(255 * c) for c in colorsys.hsv_to_rgb((i * 0.618034) % 1.0, 0.65, 0.95)) for i in range(n)]


def _tinted_crop(session: Any, leaves: Sequence[int], groups: Sequence[Sequence[int]], margin: int = 12
                 ) -> np.ndarray | None:
    """Working-copy crop around the leaf union with each group of leaves tinted its own colour and outlined."""
    union = _union_mask(session, leaves)
    bb = mask_bbox(union)
    if bb is None:
        return None
    x, y, w, h = bb
    x0, y0 = max(0, x - margin), max(0, y - margin)
    x1, y1 = min(session.width, x + w + margin), min(session.height, y + h + margin)
    crop = session.working_rgb[y0:y1, x0:x1].astype(np.float32)
    frag = session.fragments[y0:y1, x0:x1]
    cols = _palette(max(1, len(groups)))
    for gi, g in enumerate(groups):
        keep = np.zeros(session.tree.n_leaves + 1, bool)
        keep[list(g)] = True
        m = keep[frag]
        crop[m] = 0.55 * crop[m] + 0.45 * np.array(cols[gi % len(cols)], np.float32)
        edge = m & ~cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        crop[edge] = (20, 20, 20)
    out = crop.astype(np.uint8)
    outside = ~union[y0:y1, x0:x1]
    out[outside] = (0.5 * out[outside] + 0.5 * 255).astype(np.uint8)
    return out


def render_grouping_sheet(session: Any, result: dict[str, Any], out_path: Path | str, thumb: int = 110,
                          per_column: int = 6) -> Path:
    """One cell per piece: map piece | the target leaves on the drawing | the best single-level assembly
    (one colour per region to tap). Header: label, leaves, leaf IoU, area ratio; footer: the gesture
    counts. Border: green = one tap, amber = several gestures, red = placement uncertain."""
    from biomorphic.verify.contact_sheet import _piece_crop, _thumb, put_text

    objs = result["_objects"]
    map_rgb = objs["map_rgb"]
    pieces = {p.index: p for p in objs["pieces"]}
    rows = sorted(result["rows"], key=lambda r: (r["label"].startswith("#"), r["label"]))
    cell_w, cell_h = 3 * thumb + 32, thumb + 70
    n = len(rows)
    n_cols = max(1, math.ceil(n / per_column))
    width, height = n_cols * cell_w + 8, min(n, per_column) * cell_h + 60
    out = np.full((max(height, 80), max(width, 480), 3), 250, np.uint8)
    s = result["summary"]
    c = s["confident"]
    put_text(out, f"grouping cost - {result.get('source', '')} - session {result.get('session_id', '')}: "
                  f"{c['n']}/{n} placements confident; of those {c['one_region_pieces']} are one region at some "
                  f"level; single-level gestures {c['single_level']['gestures']} (median "
                  f"{c['single_level']['median_gestures'] or 0:g}), mixed {c['mixed']['gestures']}, with mirror "
                  f"co-select {c['mixed']['gestures_with_mirror_coselect']}",
             (8, 22), 0.5, (30, 30, 30), 1)
    put_text(out, "map piece | target leaves on the drawing | best single level: one colour per region to tap; "
                  "green = one tap, amber = several gestures, red = placement uncertain",
             (8, 44), 0.45, (90, 90, 90), 1)
    for k, r in enumerate(rows):
        col, row = divmod(k, per_column)
        x0, y0 = 4 + col * cell_w, 52 + row * cell_h
        single = r["single"]
        head = (205, 70, 70) if not r["confident"] else ((70, 170, 90) if single["taps"] == 1 else (235, 160, 40))
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + cell_h - 4), head, 2)
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + 22), head, -1)
        ar = "" if r.get("area_ratio") is None else f"  area x{r['area_ratio']:.2f}"
        tw = "  (twin)" if r["placed_by"] != "anchor" else ("  (mirrored)" if r["mirrored"] else "")
        put_text(out, f"{r['label']}  {r['n_leaves']} leaves  IoU {r['leaf_iou']:.2f}{ar}{tw}",
                 (x0 + 6, y0 + 16), 0.45, (255, 255, 255), 1)
        piece = pieces.get(r["piece_index"])
        out[y0 + 28:y0 + 28 + thumb, x0 + 6:x0 + 6 + thumb] = _thumb(_piece_crop(map_rgb, piece) if piece else None, thumb)
        leaves = r["leaves"]
        out[y0 + 28:y0 + 28 + thumb, x0 + 12 + thumb:x0 + 12 + 2 * thumb] = _thumb(
            _tinted_crop(session, leaves, [leaves]), thumb)
        roots = _leaf_roots(session, single["level"])
        groups = [[int(l) for l in leaves if roots[l] == rid] for rid in single["regions"]]
        out[y0 + 28:y0 + 28 + thumb, x0 + 18 + 2 * thumb:x0 + 18 + 3 * thumb] = _thumb(
            _tinted_crop(session, leaves, groups), thumb)
        m = r["mixed"]
        put_text(out, f"level {single['level']} ({single['n_regions']} parts): {single['taps']} tap"
                      f"{'s' if single['taps'] != 1 else ''}"
                      + (" + merge" if single["merges"] else "")
                      + (f"; mixed {m['gestures']} gestures over {len(m['steps'])} levels"
                         if m["gestures"] < single["gestures"] else ""),
                 (x0 + 6, y0 + 28 + thumb + 16), 0.42, (40, 40, 40), 1)
        put_text(out, f"one region at levels {_levels_str(r['one_region_levels'])}" if r["one_region_levels"]
                 else f"never one region; assemblable at {_levels_str(r['feasible_levels'])}",
                 (x0 + 6, y0 + 28 + thumb + 34), 0.4, (90, 90, 90), 1)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), cv2.cvtColor(out, cv2.COLOR_RGB2BGR)):
        raise RuntimeError(f"could not write {out_path}")
    return out_path


def _levels_str(levels: Sequence[int]) -> str:
    """Compact '0-3, 7, 9-11' for a sorted list of level indices."""
    lv = sorted(int(x) for x in levels)
    if not lv:
        return "none"
    runs: list[list[int]] = [[lv[0], lv[0]]]
    for x in lv[1:]:
        if x == runs[-1][1] + 1:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs)


def _totals_lines(name: str, t: dict[str, Any], pairs: list[dict[str, Any]]) -> list[str]:
    if not t["n"]:
        return [f"- {name}: no chunks."]
    cs = t["chunk_size"]
    pair_names = ", ".join(f"{p['a']}/{p['b']}" for p in pairs) or "none"
    return [
        f"- {name} ({t['n']} chunks): size {cs['min']}-{cs['max']} leaves, median {cs['median']:g} "
        f"(histogram: {', '.join(f'{k}: {v}' for k, v in cs['histogram'].items())}); "
        f"{t['one_region_pieces']}/{t['n']} are one region at some slider level (one tap).",
        f"  Single level: {t['single_level']['taps']} taps, {t['single_level']['gestures']} gestures in all "
        f"(median {t['single_level']['median_gestures']:g}, max {t['single_level']['max_gestures']}). "
        f"Mixed levels: {t['mixed']['gestures']} gestures (median {t['mixed']['median_gestures']:g}, max "
        f"{t['mixed']['max_gestures']}); {t['mixed']['gestures_with_mirror_coselect']} when a chunk's mirror twin "
        f"rides along for free ({len(pairs)} twin pairs found: {pair_names}).",
    ]


def write_grouping_report(result: dict[str, Any], out_dir: Path | str, sheet_name: str | None = None) -> Path:
    """Write grouping_report.md and grouping_report.json; return the markdown path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = result["summary"]
    rows = sorted(result["rows"], key=lambda r: (r["label"].startswith("#"), r["label"]))
    n = s["n_pieces"]
    rule = s["confident_rule"]
    lines = [
        f"# Grouping cost - {result.get('source', '')} - session {result.get('session_id', '')}",
        "",
        "How many gestures the review UI, as built, needs to assemble each chunk of the artist's map. A *tap* "
        "selects a region, *merge* is the Merge button, a *level change* is one slider move. The single-level "
        "number stays on one slider position; the mixed plan taps the largest clean regions at a coarse level, "
        "merges, then descends for the rest.",
        "",
        "## Summary",
        "",
        (f"- {n} chunks on the map, {s['n_leaves_total']} atomic leaves in the drawing; map to drawing scale "
         f"{s['scale']} working px per map px ({s['scale_info'].get('kept')}/{s['scale_info'].get('votes')} "
         f"confident matches agree)." if s.get("scale") else
         f"- {n} chunks on the map, {s['n_leaves_total']} atomic leaves in the drawing; no confident match fixed a scale."),
        f"- Placement confident (leaf IoU >= {rule['leaf_iou']:.2f} and area ratio in "
        f"{rule['area_ratio'][0]}-{rule['area_ratio'][1]}) for {s['confident']['n']}/{n}; uncertain: "
        f"{', '.join(s['uncertain']) or 'none'}."
        + (f" Placed through the twin map: {', '.join(s['placed_by_twin'])}." if s["placed_by_twin"] else "")
        + (f" Duplicate targets: {'; '.join('/'.join(d) for d in s['duplicate_targets'])}."
           if s["duplicate_targets"] else "")
        + (f" Unplaced: {', '.join(s['unplaced'])}." if s["unplaced"] else ""),
        *_totals_lines("Confident placements", s["confident"], s["mirror_pairs"]),
        *_totals_lines("All placements", s["all"], s["mirror_pairs"]),
        f"- Chunks the atomic split cannot reproduce (leaf IoU < {s['leaf_iou_threshold']:.2f}): "
        f"{', '.join(s['low_leaf_iou']) or 'none'}.",
    ]
    if sheet_name:
        lines += ["", f"![grouping sheet]({sheet_name})"]
    lines += [
        "",
        "## Per chunk",
        "",
        "| chunk | placed | leaves | leaf IoU | area ratio | one region at | best single level | taps | merge | mixed gestures | mixed steps |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        sg = r["single"]
        m = r["mixed"]
        steps = " > ".join(f"L{st['level']}:{st['taps']}" for st in m["steps"])
        ar = "-" if r.get("area_ratio") is None else f"{r['area_ratio']:.2f}"
        placed = ("ok" if r["confident"] else "?") + (" twin" if r["placed_by"] != "anchor" else "")
        lines.append(f"| {r['label']} | {placed} | {r['n_leaves']} | {r['leaf_iou']:.2f} | {ar} | "
                     f"{_levels_str(r['one_region_levels'])} | {sg['level']} ({sg['n_regions']} parts) | "
                     f"{sg['taps']} | {'yes' if sg['merges'] else '-'} | {m['gestures']} | {steps} |")
    lines += [
        "",
        "## Reading it",
        "",
        "- *placed*: `ok` when the chunk's location on the drawing is trusted, `?` when it is not (judge the "
        "sheet); `twin` when it was placed as the mirror twin of another chunk that had taken the same leaves.",
        "- *leaf IoU* is the shape IoU between the union of the chunk's leaves and the map piece (mirror allowed). "
        "Below the threshold, no grouping gesture reaches the artist's shape: a leaf straddles the chunk's edge. "
        "*area ratio* is the union's area over the piece's area at the map-to-drawing scale (1 = same size).",
        "- *one region at*: slider levels where the chunk is exactly one region. Empty means the merge tree never "
        "forms it: the chunk's cells join neighbours before each other.",
        "- *best single level*: the slider position that assembles the chunk with the fewest gestures without moving "
        "the slider again. Level 0 (every cell its own part) always can.",
        "",
    ]
    md = out_dir / "grouping_report.md"
    md.write_text("\n".join(lines))
    (out_dir / "grouping_report.json").write_text(json.dumps(jsonable(result), indent=1))
    return md


def run_grouping(session_dir: Path | str, map_png: Path | str, out_dir: Path | str,
                 labels_path: Path | str | None = None, **kw: Any) -> dict[str, Any]:
    """CLI entry: load the session and map, measure, write the sheet and the report."""
    from biomorphic.session import Session
    from biomorphic.verify.checks import read_rgb

    session = Session.load(session_dir)
    map_png = Path(map_png)
    labels_path = Path(labels_path) if labels_path else map_png.with_suffix(".labels.json")
    labels = gt.load_labels(labels_path) if Path(labels_path).exists() else None
    result = grouping_cost(session, read_rgb(map_png), labels, **kw)
    out_dir = Path(out_dir)
    sheet = render_grouping_sheet(session, result, out_dir / "grouping_sheet.png")
    md = write_grouping_report(result, out_dir, sheet.name)
    result["report_path"] = str(md)
    result["sheet_path"] = str(sheet)
    return result
