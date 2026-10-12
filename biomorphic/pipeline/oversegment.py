"""oversegment step: every ink-free cell becomes a marker; a watershed grows the cells out to the ink's
centre line so that neighbours share a border with no gutter. The result is the leaf fragment map.

markers   = 4-connected components of (figure AND NOT ink) with area >= cell_min_area
elevation = distance to the nearest non-ink pixel inside the ink (0 elsewhere): the ridge is the centre line
labels    = watershed(elevation, markers, mask=figure); pixels the flood did not reach (none in practice)
            are assigned to the nearest labelled pixel, so every figure pixel gets a leaf id.
Optional extra seeds split very large cells into several leaves whose shared borders carry no ink; the
merge step joins them first (near-zero cost) and level 0 is chosen after those merges. Off by default.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage.segmentation import watershed

DEFAULTS = {
    "cell_min_area": 30,            # ink-free components smaller than this (px) are not markers (speck floor)
    "extra_seeds": False,           # split large cells with extra seeds (their borders carry no ink)
    "extra_seed_min_area": 40000,   # cells at least this big (px) get extra seeds
    "extra_seed_spacing": 150,      # seed grid spacing (px) inside such cells
}


@dataclass
class OversegmentResult:
    fragments: np.ndarray     # int32 HxW, 0 outside the figure, 1..n_leaves inside
    n_leaves: int
    n_cells: int              # markers from ink-free cells (before extra seeds)
    n_markers: int            # markers actually used (cells + extra seeds)
    n_stragglers: int         # figure pixels the watershed did not reach (assigned to the nearest label)


def cell_markers(figure_mask: np.ndarray, ink_mask: np.ndarray, min_area: int) -> tuple[np.ndarray, int]:
    """Consecutively labelled ink-free cells (4-connected) of at least min_area px."""
    cells = (figure_mask & ~ink_mask).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cells, connectivity=4)
    if n <= 1:
        return np.zeros(figure_mask.shape, np.int32), 0
    keep = stats[:, cv2.CC_STAT_AREA] >= int(min_area)
    keep[0] = False
    lut = np.zeros(n, np.int32)
    lut[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    return lut[labels], int(keep.sum())


def add_extra_seeds(markers: np.ndarray, n_markers: int, min_area: int, spacing: int) -> tuple[np.ndarray, int]:
    """Replace each large cell's marker by a grid of seed discs (so the watershed splits it Voronoi-like)."""
    out = markers.copy()
    areas = np.bincount(markers.ravel(), minlength=n_markers + 1)
    next_id = n_markers + 1
    half = max(2, spacing // 2)
    for cid in range(1, n_markers + 1):
        if areas[cid] < min_area:
            continue
        cell = markers == cid
        ys, xs = np.nonzero(cell)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        sub = cell[y0:y1, x0:x1]
        dist = ndi.distance_transform_edt(sub)
        seeds = []
        for gy in range(half, sub.shape[0], spacing):
            for gx in range(half, sub.shape[1], spacing):
                if dist[gy, gx] > spacing / 4.0:
                    seeds.append((gy, gx))
        # always keep the cell's own deepest point so the cell is never left without a seed
        my, mx = np.unravel_index(int(np.argmax(dist)), dist.shape)
        seeds.append((int(my), int(mx)))
        if len(seeds) < 2:
            continue
        out[y0:y1, x0:x1][sub] = 0
        for sy, sx in seeds:
            yy, xx = np.ogrid[-2:3, -2:3]
            disc = (yy * yy + xx * xx) <= 4
            ys_, xs_ = np.nonzero(disc)
            ys_, xs_ = ys_ + sy - 2, xs_ + sx - 2
            ok = (ys_ >= 0) & (ys_ < sub.shape[0]) & (xs_ >= 0) & (xs_ < sub.shape[1])
            ys_, xs_ = ys_[ok], xs_[ok]
            inside = sub[ys_, xs_]
            out[y0 + ys_[inside], x0 + xs_[inside]] = next_id
            next_id += 1
    return out, next_id - 1


def assign_stragglers(labels: np.ndarray, figure_mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Give every unlabelled figure pixel the label of the nearest labelled pixel."""
    strag = figure_mask & (labels == 0)
    n = int(strag.sum())
    if n == 0:
        return labels, 0
    _d, (iy, ix) = ndi.distance_transform_edt(labels == 0, return_indices=True)
    out = labels.copy()
    out[strag] = labels[iy[strag], ix[strag]]
    return out, n


def relabel_consecutive(labels: np.ndarray, figure_mask: np.ndarray) -> tuple[np.ndarray, int]:
    ids = np.unique(labels[figure_mask])
    ids = ids[ids > 0]
    lut = np.zeros(int(labels.max()) + 1, np.int32)
    lut[ids] = np.arange(1, len(ids) + 1, dtype=np.int32)
    out = lut[labels]
    out[~figure_mask] = 0
    return out, int(len(ids))


def oversegment(figure_mask: np.ndarray, ink_mask: np.ndarray, ink_dist: np.ndarray,
                params: dict | None = None) -> OversegmentResult:
    p = {**DEFAULTS, **(params or {})}
    figure = figure_mask.astype(bool)
    ink = ink_mask.astype(bool) & figure
    markers, n_cells = cell_markers(figure, ink, int(p["cell_min_area"]))
    n_markers = n_cells
    if p.get("extra_seeds") and n_cells:
        markers, n_markers = add_extra_seeds(markers, n_cells, int(p["extra_seed_min_area"]),
                                             int(p["extra_seed_spacing"]))
    if n_markers == 0:
        # no ink-free cell at all: the whole figure is one leaf
        frags = figure.astype(np.int32)
        return OversegmentResult(frags, int(figure.any()), 0, 0, int(figure.sum()))
    elevation = np.where(figure, ink_dist, 0.0).astype(np.float32)
    labels = watershed(elevation, markers, mask=figure, connectivity=1).astype(np.int32)
    labels, n_strag = assign_stragglers(labels, figure)
    frags, n_leaves = relabel_consecutive(labels, figure)
    return OversegmentResult(frags, n_leaves, n_cells, n_markers, n_strag)
