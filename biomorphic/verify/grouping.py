"""Grouping cost: how many gestures the review UI needs to assemble each of the artist's chunks.

The artist's hand-made map (verify check 6) names the components they would harvest: chunks of one to a
handful of ink-enclosed cells. The slider cuts a precomputed merge tree, so a chunk is cheap when some
level shows it as one region and expensive when its cells must be tapped one by one and merged. This
module measures that cost on a session, per chunk, under the UI as built:

1. **Target.** Every map piece is shape-matched against every level (groundtruth.match_map_to_session_levels)
   and the best-scoring region anchors a projection of the piece onto the working copy: the matcher's
   (flip, rotation, shift) between normalised frames is turned into one affine map from map pixels to
   working pixels. The chunk's *target* is the set of leaf fragments mostly (>= `min_leaf_fraction` of
   their area) under the projected piece. The projection is then re-fitted on the union of those leaves and
   the target recomputed until it settles, so one weak anchor does not fix the answer. `leaf_iou` is the
   scale-normalised shape IoU of the union against the piece: how well the atomic cells can reproduce the
   chunk at all (low = the split itself disagrees with the artist, no grouping gesture fixes it).
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
project_piece(piece, transform, piece_centroid, anchor_centroid, shape) -> bool mask     map piece -> working px
target_for_piece(session, piece, level, region_id, transform) -> Target                  refined leaf set
level_cost(session, level, leaves) -> LevelCost | None                                   None = not assemblable
mixed_plan(session, leaves) -> MixedPlan
grouping_cost(session, map_rgb, labels) -> dict                                          the whole report
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


# ============================================================================= projection
def _affine_map_to_working(piece_centroid: Sequence[float], transform: gt.ShapeTransform,
                           anchor_centroid: Sequence[float], canvas: int = gt.FRAME_CANVAS) -> np.ndarray:
    """3x3 matrix taking map pixel coordinates to working pixel coordinates.

    The matcher normalises both masks (centroid at the frame centre, sqrt(area) = FRAME_SIZE), then flips,
    rotates about the frame centre and shifts the piece frame onto the candidate frame. Composing those
    with the two normalisations gives the map -> working map; cv2 conventions throughout (x right, y down,
    positive angle counter-clockwise).
    """
    n = float(canvas)
    o = n / 2 - 0.5
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
    from_frame = tr(ax, ay) @ sc(1.0 / transform.scale_b) @ tr(-n / 2, -n / 2)
    return from_frame @ shift @ rot @ flip @ to_frame


def project_piece(piece_mask: np.ndarray, transform: gt.ShapeTransform, piece_centroid: Sequence[float],
                  anchor_centroid: Sequence[float], shape: tuple[int, int]) -> np.ndarray:
    """Project a map piece mask onto the working copy (HxW bool) through the matcher's transform, anchored
    on the centroid of the region it matched."""
    piece_mask = np.asarray(piece_mask).astype(bool)
    bb = mask_bbox(piece_mask)
    if bb is None:
        raise ValueError("empty piece mask")
    x, y, w, h = bb
    m = _affine_map_to_working(piece_centroid, transform, anchor_centroid)
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
    projected: np.ndarray | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "piece_index": self.piece_index, "leaves": list(self.leaves),
                "n_leaves": len(self.leaves), "leaf_iou": round(self.leaf_iou, 4),
                "anchor_level": self.anchor_level, "anchor_region": self.anchor_region,
                "anchor_score": round(self.anchor_score, 4), "mirrored": self.mirrored,
                "iterations": self.iterations, "settled": self.settled}


def target_for_piece(session: Any, piece: Any, level: int, region_id: int, transform: gt.ShapeTransform,
                     *, anchor_score: float = 0.0, min_fraction: float = MIN_LEAF_FRACTION,
                     iters: int = REFINE_ITERS, leaf_areas: np.ndarray | None = None) -> Target:
    """Leaves of the chunk a map piece names, refined until the leaf set stops changing."""
    shape = (session.height, session.width)
    areas = _leaf_areas(session) if leaf_areas is None else leaf_areas
    info = session.regions(int(level))[int(region_id)]
    projected = project_piece(piece.mask, transform, piece.centroid, info.centroid, shape)
    leaves = leaves_under_mask(session, projected, min_fraction, areas)
    if not leaves:   # the projection missed every leaf: fall back to the anchor region itself
        leaves = [int(i) for i in info.leaves]
    settled = False
    n_iter = 0
    iou = 0.0
    for n_iter in range(1, int(iters) + 1):
        union = _union_mask(session, leaves)
        iou, tr = gt.shape_score(piece.mask, union)
        projected = project_piece(piece.mask, tr, piece.centroid, _centroid(union), shape)
        new = leaves_under_mask(session, projected, min_fraction, areas) or leaves
        if new == leaves:
            settled = True
            break
        leaves = new
    if not settled:
        iou, _tr = gt.shape_score(piece.mask, _union_mask(session, leaves))
    return Target(label=piece.name, piece_index=int(piece.index), leaves=tuple(sorted(leaves)),
                  leaf_iou=float(iou), anchor_level=int(level), anchor_region=int(region_id),
                  anchor_score=float(anchor_score), mirrored=bool(transform.mirrored),
                  iterations=n_iter, settled=settled, projected=projected)


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


# ============================================================================= mirror pairs between chunks
def mirror_pairs(session: Any, targets: Sequence[Target], min_fraction: float = MIRROR_PAIR_FRACTION
                 ) -> list[tuple[str, str, float]]:
    """(label a, label b, fraction) for chunks whose leaves are each other's twins at level 0."""
    try:
        twin = session.pairing(0)["twin"]
    except Exception:  # noqa: BLE001 - no fold / pairing unavailable: report no pairs rather than fail
        return []
    roots = _leaf_roots(session, 0)
    region_leaves: dict[int, set[int]] = {}
    for leaf in range(1, roots.size):
        region_leaves.setdefault(int(roots[leaf]), set()).add(leaf)
    twin_leaves: dict[int, set[int]] = {}
    for rid, leaves in region_leaves.items():
        t = twin.get(rid) if isinstance(twin, dict) else None
        if t is None:
            t = twin.get(str(rid)) if isinstance(twin, dict) else None
        if t is not None:
            tl = region_leaves.get(int(t), set())
            for leaf in leaves:
                twin_leaves[leaf] = tl
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
            f = len(folded & lb) / max(1, len(lb))
            g = len(folded & lb) / max(1, len(la))
            score = min(f, g)
            if score > best_f:
                best, best_f = b, score
        if best is not None and best_f >= min_fraction:
            out.append((a, best, round(best_f, 3)))
    return out


# ============================================================================= the whole measurement
def grouping_cost(session: Any, map_rgb: np.ndarray, labels: dict[str, Any] | None = None, *,
                  min_fraction: float = MIN_LEAF_FRACTION, max_seconds: float = 90.0) -> dict[str, Any]:
    """Measure the grouping cost of every map piece on a session. Returns a JSON-able dict (live objects
    under `_objects`, stripped by checks.jsonable)."""
    res = gt.match_map_to_session_levels(map_rgb, session, labels, max_seconds=max_seconds,
                                         must_include=[0])
    pieces: list[Any] = list(res["_pieces"])
    tables: dict[int, gt.MatchTable] = res["_tables"]
    areas = _leaf_areas(session)
    rows: list[dict[str, Any]] = []
    targets: list[Target] = []
    for p in pieces:
        best_lv, best_row = None, None
        for lv, table in sorted(tables.items()):
            row = table.rows[p.index - 1] if p.index - 1 < len(table.rows) else None
            if row is None or row.piece_index != p.index:
                row = next((r for r in table.rows if r.piece_index == p.index), None)
            if row is None:
                continue
            if best_row is None or row.score > best_row.score:
                best_lv, best_row = int(lv), row
        if best_row is None:
            continue
        t = target_for_piece(session, p, best_lv, int(best_row.tile_id), best_row.transform,
                             anchor_score=float(best_row.score), min_fraction=min_fraction, leaf_areas=areas)
        targets.append(t)
        single = best_single_level(session, t.leaves)
        mixed = mixed_plan(session, t.leaves)
        per_level = {lv: level_cost(session, lv, t.leaves) for lv in range(len(session.levels))}
        feasible = [lv for lv, c in per_level.items() if c is not None]
        one_region_levels = [lv for lv, c in per_level.items() if c is not None and c.taps == 1]
        rows.append({
            **t.to_json(),
            "area_px": int(areas[list(t.leaves)].sum()) if t.leaves else 0,
            "single": single.to_json(), "mixed": mixed.to_json(),
            "feasible_levels": feasible, "one_region_levels": one_region_levels,
            "one_region": bool(one_region_levels),
            "per_level_taps": {str(lv): (None if c is None else c.taps) for lv, c in per_level.items()},
        })
    pairs = mirror_pairs(session, targets)
    paired_second = {b for _a, b, _f in pairs}
    n = len(rows)
    taps_single = sum(r["single"]["taps"] for r in rows)
    gest_single = sum(r["single"]["gestures"] for r in rows)
    gest_mixed = sum(r["mixed"]["gestures"] for r in rows)
    gest_mixed_pairs = sum(r["mixed"]["gestures"] for r in rows if r["label"] not in paired_second)
    sizes = [r["n_leaves"] for r in rows]
    low_iou = [r["label"] for r in rows if r["leaf_iou"] < gt.DEFAULT_THRESHOLD]
    summary = {
        "n_pieces": n, "n_leaves_total": int(session.tree.n_leaves),
        "chunk_size": {"min": min(sizes) if sizes else None, "median": float(np.median(sizes)) if sizes else None,
                       "max": max(sizes) if sizes else None,
                       "histogram": {str(k): int(v) for k, v in zip(*np.unique(sizes, return_counts=True))}},
        "one_region_pieces": sum(r["one_region"] for r in rows),
        "single_level": {"taps": taps_single, "gestures": gest_single,
                         "median_gestures": float(np.median([r["single"]["gestures"] for r in rows])) if rows else None,
                         "max_gestures": max((r["single"]["gestures"] for r in rows), default=None)},
        "mixed": {"gestures": gest_mixed,
                  "median_gestures": float(np.median([r["mixed"]["gestures"] for r in rows])) if rows else None,
                  "max_gestures": max((r["mixed"]["gestures"] for r in rows), default=None),
                  "gestures_with_mirror_coselect": gest_mixed_pairs},
        "mirror_pairs": [{"a": a, "b": b, "fraction": f} for a, b, f in pairs],
        "low_leaf_iou": low_iou, "leaf_iou_threshold": gt.DEFAULT_THRESHOLD,
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
    (one colour per region to tap). Header: label, leaves, leaf IoU; footer: the gesture counts."""
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
    put_text(out, f"grouping cost - {result.get('source', '')} - session {result.get('session_id', '')}: "
                  f"{s['one_region_pieces']}/{n} chunks are one region at some level; single-level gestures "
                  f"{s['single_level']['gestures']} (median {s['single_level']['median_gestures']:g}), "
                  f"mixed-level {s['mixed']['gestures']}, with mirror co-select {s['mixed']['gestures_with_mirror_coselect']}",
             (8, 22), 0.5, (30, 30, 30), 1)
    put_text(out, "map piece | target leaves on the drawing | best single level: one colour per region to tap",
             (8, 44), 0.45, (90, 90, 90), 1)
    for k, r in enumerate(rows):
        col, row = divmod(k, per_column)
        x0, y0 = 4 + col * cell_w, 52 + row * cell_h
        single = r["single"]
        ok = r["leaf_iou"] >= s["leaf_iou_threshold"]
        head = (70, 170, 90) if single["taps"] == 1 else ((235, 160, 40) if ok else (205, 70, 70))
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + cell_h - 4), head, 2)
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + 22), head, -1)
        put_text(out, f"{r['label']}  {r['n_leaves']} leaves  leaf IoU {r['leaf_iou']:.2f}"
                      + ("  (mirrored)" if r["mirrored"] else ""), (x0 + 6, y0 + 16), 0.45, (255, 255, 255), 1)
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
                      + (f" + merge" if single["merges"] else "")
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


def write_grouping_report(result: dict[str, Any], out_dir: Path | str, sheet_name: str | None = None) -> Path:
    """Write grouping_report.md and grouping_report.json; return the markdown path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = result["summary"]
    rows = sorted(result["rows"], key=lambda r: (r["label"].startswith("#"), r["label"]))
    n = s["n_pieces"]
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
        f"- {n} chunks on the map, {s['n_leaves_total']} atomic leaves in the drawing.",
        f"- Chunk size: {s['chunk_size']['min']}-{s['chunk_size']['max']} leaves, median {s['chunk_size']['median']:g} "
        f"(histogram: {', '.join(f'{k}: {v}' for k, v in s['chunk_size']['histogram'].items())}).",
        f"- {s['one_region_pieces']}/{n} chunks appear as one region at some slider level (one tap).",
        f"- Single level: {s['single_level']['taps']} taps, {s['single_level']['gestures']} gestures in all "
        f"(median {s['single_level']['median_gestures']:g}, max {s['single_level']['max_gestures']}).",
        f"- Mixed levels: {s['mixed']['gestures']} gestures (median {s['mixed']['median_gestures']:g}, "
        f"max {s['mixed']['max_gestures']}); {s['mixed']['gestures_with_mirror_coselect']} when a chunk's mirror "
        f"twin rides along for free ({len(s['mirror_pairs'])} twin pairs found: "
        f"{', '.join(f'{p['a']}/{p['b']}' for p in s['mirror_pairs']) or 'none'}).",
        f"- Chunks the atomic split cannot reproduce (leaf IoU < {s['leaf_iou_threshold']:.2f}): "
        f"{', '.join(s['low_leaf_iou']) or 'none'}.",
    ]
    if sheet_name:
        lines += ["", f"![grouping sheet]({sheet_name})"]
    lines += [
        "",
        "## Per chunk",
        "",
        "| chunk | leaves | leaf IoU | one region at | best single level | taps | merge | mixed gestures | mixed steps |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        sg = r["single"]
        m = r["mixed"]
        steps = " > ".join(f"L{st['level']}:{st['taps']}" for st in m["steps"])
        lines.append(f"| {r['label']} | {r['n_leaves']} | {r['leaf_iou']:.2f} | {_levels_str(r['one_region_levels'])} | "
                     f"{sg['level']} ({sg['n_regions']} parts) | {sg['taps']} | {'yes' if sg['merges'] else '-'} | "
                     f"{m['gestures']} | {steps} |")
    lines += [
        "",
        "## Reading it",
        "",
        "- *leaf IoU* is the shape IoU between the union of the chunk's leaves and the map piece (mirror allowed). "
        "Below the threshold, no grouping gesture reaches the artist's shape: a leaf straddles the chunk's edge.",
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
