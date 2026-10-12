"""merge step: region adjacency graph over the leaf fragments and the full hierarchical merge tree.

Per edge: border_len (number of 4-adjacent pixel pairs on the shared border) and ink_sum (sum of the ink
half-thickness, i.e. the ink distance-transform value, over those pairs; 0 where the border is off-ink).
Per region: area, perimeter (pairs with a different label, background included), bbox, centroid and the
side relative to the axis.

Cost of merging a and b, dominated by the ink on the shared border:
    ink_norm = (ink_sum / border_len) / t0                 ~1 for a full-thickness line, ~0 for no ink
    embed    = border_len / min(perimeter_a, perimeter_b)  1 = the smaller region is enclosed by the other
    cost = (ink_norm + merge_ink_floor)
           * (1 + merge_w_embed * (1 - embed))                     more embedded -> cheaper
           * (min(area_a, area_b) / area_ref) ** merge_w_size       smaller -> cheaper
           * (1 + merge_axis_penalty * [a and b lie on opposite sides of the axis])
Agglomeration uses a heap with lazy invalidation (an entry is stale as soon as one of its regions has
merged). Borders carrying less ink than level0_max_ink_norm are not drawn boundaries (extra seeds, specks),
so they are merged first, in a phase of their own; level 0 is the partition after that phase. The recorded
cost is max(raw, previous) so the merge list is non-decreasing (prefix semantics).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from biomorphic.session import MergeTree

DEFAULTS = {
    "merge_ink_floor": 0.05,      # added to ink_norm so off-ink merges still order by the modifiers
    "merge_w_embed": 1.0,         # weight of the embeddedness modifier (0 disables)
    "merge_w_size": 0.3,          # exponent of the size modifier (0 disables)
    "merge_area_ref_frac": 0.01,  # size reference = this fraction of the figure area
    "merge_axis_penalty": 1.0,    # extra cost factor for merging an L region with an R region
    "merge_side_frac": 0.2,       # a region with less than this fraction of its area across the axis is L or R
    "levels": 24,                 # number of slider levels
    "level_min_regions": 5,       # coarsest level has about this many regions
    "level0_max_ink_norm": 0.1,   # level 0 = after the leading merges whose border carries less ink than this
}


@dataclass
class Region:
    area: int
    perimeter: int
    x0: int
    y0: int
    x1: int
    y1: int
    sx: float
    sy: float
    area_left: int
    area_right: int
    side: str = "axis"
    alive: bool = True
    nbrs: dict = field(default_factory=dict)   # neighbour id -> [border_len, ink_sum]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1 - self.x0, self.y1 - self.y0

    @property
    def centroid(self) -> tuple[float, float]:
        return self.sx / max(1, self.area), self.sy / max(1, self.area)


def side_of(area_left: int, area_right: int, side_frac: float) -> str:
    total = max(1, area_left + area_right)
    if area_right / total < side_frac:
        return "L"
    if area_left / total < side_frac:
        return "R"
    return "axis"


def build_rag(fragments: np.ndarray, ink_dist: np.ndarray, axis_x: float, side_frac: float = 0.2) -> tuple[dict, dict]:
    """Regions {id: Region} (with neighbour edges filled in) and edges {(lo, hi): [border_len, ink_sum]}."""
    f = fragments.astype(np.int64)
    n = int(f.max())
    d = ink_dist.astype(np.float64)
    # horizontal and vertical 4-adjacent pairs with different labels
    hl, hr = f[:, :-1], f[:, 1:]
    hm = hl != hr
    vu, vd = f[:-1, :], f[1:, :]
    vm = vu != vd
    a = np.concatenate([hl[hm], vu[vm]])
    b = np.concatenate([hr[hm], vd[vm]])
    val = np.concatenate([(d[:, :-1] + d[:, 1:])[hm], (d[:-1, :] + d[1:, :])[vm]]) * 0.5
    perim = np.bincount(a, minlength=n + 1) + np.bincount(b, minlength=n + 1)
    both = (a > 0) & (b > 0)
    lo = np.minimum(a[both], b[both])
    hi = np.maximum(a[both], b[both])
    key = lo * (n + 1) + hi
    uk, inv = np.unique(key, return_inverse=True)
    border_len = np.bincount(inv, minlength=len(uk))
    ink_sum = np.bincount(inv, weights=val[both], minlength=len(uk))
    edges: dict[tuple[int, int], list] = {}
    for k, bl, isum in zip(uk.tolist(), border_len.tolist(), ink_sum.tolist()):
        edges[(k // (n + 1), k % (n + 1))] = [int(bl), float(isum)]
    # per-region stats
    flat = f.ravel()
    area = np.bincount(flat, minlength=n + 1)
    h, w = f.shape
    xs = np.tile(np.arange(w, dtype=np.float64), h)
    ys = np.repeat(np.arange(h, dtype=np.float64), w)
    sx = np.bincount(flat, weights=xs, minlength=n + 1)
    sy = np.bincount(flat, weights=ys, minlength=n + 1)
    left_cols = (np.arange(w) + 0.5) < float(axis_x)
    left = np.tile(left_cols, h)
    area_left = np.bincount(flat[left], minlength=n + 1)
    area_right = area - area_left
    objs = ndi.find_objects(fragments.astype(np.int32), max_label=n)
    regions: dict[int, Region] = {}
    for rid in range(1, n + 1):
        sl = objs[rid - 1]
        if sl is None or area[rid] == 0:
            continue
        reg = Region(area=int(area[rid]), perimeter=int(perim[rid]), x0=int(sl[1].start), y0=int(sl[0].start),
                     x1=int(sl[1].stop), y1=int(sl[0].stop), sx=float(sx[rid]), sy=float(sy[rid]),
                     area_left=int(area_left[rid]), area_right=int(area_right[rid]))
        reg.side = side_of(reg.area_left, reg.area_right, side_frac)
        regions[rid] = reg
    for (p, q), e in edges.items():
        regions[p].nbrs[q] = e
        regions[q].nbrs[p] = e
    return regions, edges


def edge_cost(ra: Region, rb: Region, border_len: int, ink_sum: float, t0: float, area_ref: float,
              p: dict) -> tuple[float, dict]:
    ink_norm = (ink_sum / max(1, border_len)) / max(1e-6, t0)
    embed = min(1.0, border_len / max(1, min(ra.perimeter, rb.perimeter)))
    size = (min(ra.area, rb.area) / max(1.0, area_ref)) ** float(p["merge_w_size"])
    cross = (ra.side == "L" and rb.side == "R") or (ra.side == "R" and rb.side == "L")
    cost = (ink_norm + float(p["merge_ink_floor"])) * (1.0 + float(p["merge_w_embed"]) * (1.0 - embed)) \
        * size * (1.0 + float(p["merge_axis_penalty"]) * (1.0 if cross else 0.0))
    return float(cost), {"ink_norm": ink_norm, "embed": embed, "size": size, "cross": bool(cross)}


@dataclass
class MergeResult:
    tree: MergeTree
    stats: list            # per merge: {"a","b","new","raw_cost","ink_norm","embed","size","cross","border_len"}
    n_disconnected: int    # merges forced because the adjacency graph was not connected


def build_merge_tree(fragments: np.ndarray, ink_dist: np.ndarray, t0: float, axis_x: float,
                     params: dict | None = None) -> MergeResult:
    p = {**DEFAULTS, **(params or {})}
    regions, edges = build_rag(fragments, ink_dist, axis_x, float(p["merge_side_frac"]))
    n = int(fragments.max())
    figure_area = float(sum(r.area for r in regions.values()))
    area_ref = max(1.0, figure_area * float(p["merge_area_ref_frac"]))
    no_ink = float(p["level0_max_ink_norm"])

    def key(cost: float, info: dict, a: int, b: int) -> tuple[int, float, int, int]:
        return (0 if info["ink_norm"] < no_ink else 1, cost, min(a, b), max(a, b))

    heap: list[tuple[int, float, int, int]] = []
    for (a, b), (bl, isum) in edges.items():
        c, info = edge_cost(regions[a], regions[b], bl, isum, t0, area_ref, p)
        heap.append(key(c, info, a, b))
    heapq.heapify(heap)
    alive = sum(1 for r in regions.values() if r.alive)
    merges: list[list] = []
    stats: list[dict] = []
    prev = 0.0
    n_disc = 0
    next_id = n + 1

    def do_merge(a: int, b: int, raw: float, info: dict | None) -> None:
        nonlocal prev, next_id, alive
        ra, rb = regions[a], regions[b]
        e = ra.nbrs.get(b, [0, 0.0])
        new = Region(area=ra.area + rb.area, perimeter=ra.perimeter + rb.perimeter - 2 * int(e[0]),
                     x0=min(ra.x0, rb.x0), y0=min(ra.y0, rb.y0), x1=max(ra.x1, rb.x1), y1=max(ra.y1, rb.y1),
                     sx=ra.sx + rb.sx, sy=ra.sy + rb.sy, area_left=ra.area_left + rb.area_left,
                     area_right=ra.area_right + rb.area_right)
        new.side = side_of(new.area_left, new.area_right, float(p["merge_side_frac"]))
        nid = next_id
        next_id += 1
        for c in set(ra.nbrs) | set(rb.nbrs):
            if c in (a, b):
                continue
            ea = ra.nbrs.get(c)
            eb = rb.nbrs.get(c)
            merged = [(ea[0] if ea else 0) + (eb[0] if eb else 0), (ea[1] if ea else 0.0) + (eb[1] if eb else 0.0)]
            rc = regions[c]
            rc.nbrs.pop(a, None)
            rc.nbrs.pop(b, None)
            rc.nbrs[nid] = merged
            new.nbrs[c] = merged
            cost, info_c = edge_cost(new, rc, merged[0], merged[1], t0, area_ref, p)
            heapq.heappush(heap, key(cost, info_c, nid, c))
        ra.alive = rb.alive = False
        ra.nbrs = {}
        rb.nbrs = {}
        regions[nid] = new
        alive -= 1
        rec = max(float(raw), prev)
        prev = rec
        merges.append([int(a), int(b), rec, int(nid)])
        stats.append({"a": int(a), "b": int(b), "new": int(nid), "raw_cost": float(raw), "border_len": int(e[0]),
                      **({k: (float(v) if not isinstance(v, bool) else v) for k, v in info.items()} if info else
                         {"ink_norm": 0.0, "embed": 0.0, "size": 0.0, "cross": False})})

    while alive > 1 and heap:
        _phase, cost, a, b = heapq.heappop(heap)
        ra, rb = regions.get(a), regions.get(b)
        if ra is None or rb is None or not ra.alive or not rb.alive or b not in ra.nbrs:
            continue
        bl, isum = ra.nbrs[b]
        _c, info = edge_cost(ra, rb, bl, isum, t0, area_ref, p)
        do_merge(a, b, cost, info)
    if alive > 1:
        # disconnected adjacency graph (should not happen for a connected figure): join the pieces at the end
        rest = [rid for rid, r in regions.items() if r.alive]
        while len(rest) > 1:
            a, b = rest[0], rest[1]
            do_merge(a, b, prev, None)
            n_disc += 1
            rest = [next_id - 1] + rest[2:]
    tree = MergeTree(n, merges)
    tree.validate()
    return MergeResult(tree=tree, stats=stats, n_disconnected=n_disc)


def choose_levels(tree: MergeTree, stats: list, params: dict | None = None) -> list[dict]:
    """~`levels` levels: level 0 after the leading off-ink merges, then geometric in n_regions down to ~min."""
    p = {**DEFAULTS, **(params or {})}
    n = tree.n_leaves
    n0 = 0
    for s in stats:
        if s["ink_norm"] < float(p["level0_max_ink_norm"]):
            n0 += 1
        else:
            break
    r0 = n - n0
    n_levels = max(1, int(p["levels"]))
    r_min = max(1, min(int(p["level_min_regions"]), r0))
    targets = [r0]
    if n_levels > 1 and r0 > r_min:
        for i in range(1, n_levels):
            targets.append(int(round(r0 * (r_min / r0) ** (i / (n_levels - 1)))))
    levels: list[dict] = []
    seen: set[int] = set()
    for r in targets:
        r = max(r_min, min(r0, r))
        nm = tree.n_merges_for_regions(r)
        if nm in seen:
            continue
        seen.add(nm)
        levels.append({"index": len(levels), "n_merges": int(nm), "n_regions": int(n - nm),
                       "cost": round(tree.cost_at(nm), 6)})
    return levels
