"""ink step: the black-ink mask. Ink = dark AND colourless in CIE Lab, both measured relative to the paper.

Why "relative to the paper": the photo's white balance leaves the paper with a warm tint (on the sample
L~86, a~4, b~16). A colourless surface photographed under the same light keeps that tint scaled down with
its lightness ((L+16)/(L_paper+16), the cube-root law of Lab), so the darkest ink lands near (a, b) ~ (1, 1),
not near the paper's (4, 16) and not exactly at 0. Measuring chroma as the distance from the scaled paper
tint keeps ink inside the threshold while navy pencil (b ~ -16 at the same lightness) stays well outside.
Erring toward *not* calling dark colour ink is deliberate: the prototypes' worst failure was navy pencil
and colour gradients becoming boundaries.

Two refinements, both measured on two drawings (cool fills on paper, warm fills on wood):
- Colour bleed. A phone photo stores colour at lower resolution than lightness, so a thin black stroke
  between saturated fills picks up their colour (relative chroma 15-30 at the stroke centre). A dark pixel
  is still colourless when its chroma is well below that of the most colourful fill around it
  (ink_rel_chroma_frac). Navy pencil is about as colourful as the pale fill it shades, so it stays out.
- Dark fills. Dark brown or plum fills can be as dark and dull as ink. Ink is a thin dark structure: its
  black-hat contrast (closing of L minus L, element ~3 stroke widths) is high, while the inside of a fill is
  flat. Truly black pixels pass regardless, so thick ink and junctions stay ink, and any small hole this test
  opens inside the old mask (dark shading beside a line) is refilled rather than becoming a fake cell.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from skimage.morphology import skeletonize

DEFAULTS = {
    "ink_max_L_frac": 0.41,     # dark: L < this fraction of the paper's L (0.41 * 86 = 35 on the sample)
    "ink_max_chroma": 14.0,     # colourless: (a, b) within this distance of the paper tint scaled to the pixel's L
    "ink_rel_chroma_frac": 0.55,  # ... or chroma below this fraction of the most colourful fill nearby (0 = off)
    "ink_thin_contrast": 10.0,  # ink is thin: black-hat of L above this, unless truly black (0 = off)
    "ink_black_L_frac": 0.25,   # truly black: L below this fraction of the paper's L ...
    "ink_black_chroma": 10.0,   # ... and relative chroma below this
    "ink_fill_width_t0": 2.5,   # a dark area wider than 2 * this * t0 is a dark fill, not ink (0 = off) ...
    "ink_fill_min_area_r2": 40.0,  # ... when it covers at least this many squared radii; its rim stays ink
    "ink_speck_area": 24,       # ink components smaller than this (px) are specks, not ink
    "ink_close_px": 1,          # close gaps up to this size (px) in the ink mask
    "ink_bridge": True,         # bridge short gaps where a line stops and continues (see bridge_gaps)
    "ink_bridge_radius_t0": 3.0,  # bridges up to this many half-thicknesses + 4 px long ...
    "ink_bridge_max_angle": 60.0,  # ... within this angle of the line's heading at its end ...
    "ink_bridge_max_L_frac": 0.65,  # ... over pixels no lighter than this fraction of the paper's L ...
    "ink_bridge_mean_L_frac": 0.55,  # ... and this dark on average
}


@dataclass
class InkResult:
    mask: np.ndarray        # bool HxW
    dist: np.ndarray        # float32 HxW: distance to the nearest non-ink pixel inside ink (0 outside)
    t0: float               # median ink half-thickness (px) measured on the ink skeleton
    raw_fraction: float     # fraction of pixels classified as ink before cleaning (diagnostic)
    n_bridges: int = 0      # line gaps closed by bridge_gaps


def colourless_distance(lab: np.ndarray, paper: np.ndarray) -> np.ndarray:
    """Distance of each pixel's (a, b) from the paper tint scaled to the pixel's lightness."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    k = np.clip((L + 16.0) / (float(paper[0]) + 16.0), 0.0, 1.0)
    return np.hypot(a - float(paper[1]) * k, b - float(paper[2]) * k)


def classify_ink(lab: np.ndarray, paper: np.ndarray, max_L_frac: float, max_chroma: float) -> np.ndarray:
    """Raw per-pixel ink classification (dark and colourless relative to the paper)."""
    dark = lab[..., 0] < max_L_frac * float(paper[0])
    return dark & (colourless_distance(lab, paper) < max_chroma)


def clean_mask(mask: np.ndarray, speck_area: int, close_px: int) -> np.ndarray:
    m = mask.astype(np.uint8)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * close_px + 1, 2 * close_px + 1))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    if speck_area > 1:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = stats[:, cv2.CC_STAT_AREA] >= speck_area
        keep[0] = False
        m = keep[labels].astype(np.uint8)
    return m > 0


def ink_distance(mask: np.ndarray) -> np.ndarray:
    """Euclidean distance to the nearest non-ink pixel, for ink pixels (0 elsewhere). Ridge = centre line."""
    return cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5).astype(np.float32)


def half_thickness(mask: np.ndarray, dist: np.ndarray) -> float:
    """Median of the distance transform on the ink skeleton: the typical half-thickness of a drawn line."""
    if not mask.any():
        return 1.0
    skel = skeletonize(mask)
    vals = dist[skel]
    if vals.size == 0:
        return float(max(1.0, dist.max()))
    return float(max(1.0, np.median(vals)))


def _ellipse(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def refill_small_holes(mask: np.ndarray, allowed: np.ndarray, max_area: int) -> np.ndarray:
    """Give back to the mask the pixels of `allowed` that lie in non-mask components of at most max_area px."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    small = np.zeros(n, bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] <= max_area
    return mask | (small[labels] & allowed)


def dark_fill_cores(dark: np.ndarray, t0: float, width_t0: float, min_area_r2: float) -> np.ndarray:
    """Interiors of dark areas far wider than any stroke (a fill as dark as the ink, e.g. deep plum).

    A disk of radius width_t0 * t0 fits inside such an area; large areas where it does are fills. Their
    interior is returned; a rim of one stroke width is left out of it so the fill stays walled off from its
    neighbours by ink."""
    r = max(2, int(round(width_t0 * t0)))
    opened = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, _ellipse(r)) > 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened.astype(np.uint8), connectivity=8)
    large = np.zeros(n, bool)
    large[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area_r2 * r * r
    return cv2.erode(large[labels].astype(np.uint8), _ellipse(max(1, int(round(2 * t0))))) > 0


def detect_ink(lab: np.ndarray, paper: np.ndarray, params: dict | None = None,
               figure: np.ndarray | None = None) -> InkResult:
    """Ink mask. With `figure`, thinness is judged against the drawing only: the bright paper or board outside
    would otherwise make every dark fill along the outline look like a thin line."""
    p = {**DEFAULTS, **(params or {})}
    speck, close = int(p["ink_speck_area"]), int(p["ink_close_px"])
    L = lab[..., 0]
    rel = colourless_distance(lab, paper)
    dark = L < float(p["ink_max_L_frac"]) * float(paper[0])
    base = dark & (rel < float(p["ink_max_chroma"]))
    rel_frac, thin_c = float(p["ink_rel_chroma_frac"]), float(p["ink_thin_contrast"])
    if rel_frac <= 0 and thin_c <= 0:
        mask = clean_mask(base, speck, close)
        dist = ink_distance(mask)
        return InkResult(mask=mask, dist=dist, t0=half_thickness(mask, dist), raw_fraction=float(base.mean()))
    # the stroke width of the conservative mask scales both neighbourhoods
    first = clean_mask(base, speck, close)
    t1 = half_thickness(first, ink_distance(first))
    colourless = rel < float(p["ink_max_chroma"])
    if rel_frac > 0:
        local_max = cv2.dilate(rel.astype(np.float32), _ellipse(int(round(3 * t1)) + 3))
        colourless |= rel < rel_frac * local_max
    raw = dark & colourless
    allowed = clean_mask(raw, speck, close)
    if thin_c > 0:
        Lb = L.astype(np.float32) if figure is None else np.where(figure, L, 0.0).astype(np.float32)
        blackhat = cv2.morphologyEx(Lb, cv2.MORPH_BLACKHAT, _ellipse(int(round(3 * t1)) + 2))
        black = (L < float(p["ink_black_L_frac"]) * float(paper[0])) & (rel < float(p["ink_black_chroma"]))
        raw = raw & ((blackhat > thin_c) | black)
    mask = clean_mask(raw, speck, close)
    if thin_c > 0:
        mask = refill_small_holes(mask, allowed, int(np.pi * (3 * t1) ** 2))
    if float(p["ink_fill_width_t0"]) > 0:
        mask &= ~dark_fill_cores(allowed if figure is None else allowed & figure, t1,
                                 float(p["ink_fill_width_t0"]), float(p["ink_fill_min_area_r2"]))
    dist = ink_distance(mask)
    t0 = half_thickness(mask, dist)
    return InkResult(mask=mask, dist=dist, t0=t0, raw_fraction=float(raw.mean()))


# ----------------------------------------------------------------------------- gap bridging
_NBR = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def line_ends(mask: np.ndarray, spur_len: int) -> list[tuple[int, int, float, float, list]]:
    """Ends of the ink skeleton that are true line ends (not spurs): (y, x, heading dy, heading dx, branch).

    A skeleton end whose walk back meets a junction within spur_len steps is a spur of a thick stroke and is
    skipped; for the rest, the heading points from spur_len steps back towards the end.
    """
    sk = skeletonize(mask).astype(np.uint8)
    nb = cv2.filter2D(sk.astype(np.float32), -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    h, w = sk.shape
    out = []
    for y, x in zip(*np.nonzero((sk > 0) & (nb == 2))):
        path = [(int(y), int(x))]
        prev = None
        cy, cx = int(y), int(x)
        spur = False
        for _ in range(spur_len):
            nxt = [(cy + dy, cx + dx) for dy, dx in _NBR
                   if 0 <= cy + dy < h and 0 <= cx + dx < w and sk[cy + dy, cx + dx] and (cy + dy, cx + dx) != prev
                   and (cy + dy, cx + dx) not in path[-3:]]
            if not nxt:
                break
            if len(nxt) >= 2 or nb[nxt[0]] >= 4:
                spur = True
                break
            prev = (cy, cx)
            cy, cx = nxt[0]
            path.append((cy, cx))
        if spur or len(path) < 3:
            continue
        hy, hx = float(y - path[-1][0]), float(x - path[-1][1])
        n = max(1e-6, float(np.hypot(hy, hx)))
        out.append((int(y), int(x), hy / n, hx / n, path))
    return out


def bridge_gaps(mask: np.ndarray, lab: np.ndarray, paper: np.ndarray, t0: float,
                params: dict | None = None) -> tuple[np.ndarray, int]:
    """Close short gaps in drawn lines: from every true line end, a straight bridge to other ink that roughly
    continues the line's heading and runs over dark pixels only (a pale fill blocks it). Returns (mask, n)."""
    p = {**DEFAULTS, **(params or {})}
    L = lab[..., 0]
    radius = float(p["ink_bridge_radius_t0"]) * float(t0) + 4.0
    max_l = float(p["ink_bridge_max_L_frac"]) * float(paper[0])
    mean_l = float(p["ink_bridge_mean_L_frac"]) * float(paper[0])
    cos_max = float(np.cos(np.radians(float(p["ink_bridge_max_angle"]))))
    spur_len = int(round(3 * t0 + 3))
    h, w = mask.shape
    thick = max(2, int(round(t0)))
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    rr = int(np.ceil(radius))
    for y, x, hy, hx, path in line_ends(mask, spur_len):
        y0, y1, x0, x1 = max(0, y - rr), min(h, y + rr + 1), max(0, x - rr), min(w, x + rr + 1)
        ty, tx = np.nonzero(mask[y0:y1, x0:x1])
        ty, tx = ty + y0, tx + x0
        vy, vx = ty - y, tx - x
        dist = np.hypot(vy, vx)
        ok = (dist >= 2) & (dist <= radius)
        ok &= (vy * hy + vx * hx) >= cos_max * dist                       # ahead of the end
        # not the line's own end: farther than its stroke from the walked branch
        bp = np.array(path, dtype=np.float64)
        if ok.any():
            cand = np.nonzero(ok)[0]
            dd = np.min(np.hypot(ty[cand, None] - bp[None, :, 0], tx[cand, None] - bp[None, :, 1]), axis=1)
            ok[cand[dd <= t0 + 2]] = False
        if not ok.any():
            continue
        cand = np.nonzero(ok)[0]
        k = int(np.ceil(dist[cand].max())) + 1
        tt = np.linspace(0.0, 1.0, k)
        sy = np.rint(y + vy[cand, None] * tt[None, :]).astype(np.int64)
        sx = np.rint(x + vx[cand, None] * tt[None, :]).astype(np.int64)
        vals = L[sy, sx]
        valid = (vals.max(axis=1) <= max_l) & (vals.mean(axis=1) <= mean_l)
        if not valid.any():
            continue
        score = dist[cand] * (1.0 + vals.mean(axis=1) / max(1.0, float(paper[0])))
        j = int(np.argmin(np.where(valid, score, np.inf)))
        segments.append(((x, y), (int(tx[cand[j]]), int(ty[cand[j]]))))
    return keep_completing_bridges(mask, segments, thick, t0, int(p["ink_speck_area"]))


def keep_completing_bridges(mask: np.ndarray, segments: list, thick: int, t0: float, speck: int
                            ) -> tuple[np.ndarray, int]:
    """Add the bridges, then undo every bridge that only chips a scrap off a cell: a cell split by bridges must
    come apart into real components (at least 4 specks in area and wider than a sliver, radius > t0 + 2).
    Bridges that split nothing change no component and are dropped too. All bridges are judged together,
    because a cell with two gaps only comes apart when both are bridged."""
    if not segments:
        return mask, 0
    h, w = mask.shape
    stamps = []
    pad = thick + 1
    for (ax, ay), (bx, by) in segments:
        x0, y0 = max(0, min(ax, bx) - pad), max(0, min(ay, by) - pad)
        x1, y1 = min(w, max(ax, bx) + pad + 1), min(h, max(ay, by) + pad + 1)
        canvas = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.line(canvas, (ax - x0, ay - y0), (bx - x0, by - y0), 1, thick)
        ys, xs = np.nonzero(canvas)
        stamps.append((ys + y0, xs + x0))
    n0, before, _st, _c = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    alive = list(range(len(stamps)))
    min_area, min_r = 4 * speck, float(t0) + 2.0
    from scipy import ndimage as ndi
    for _ in range(4):
        cur = mask.copy()
        for i in alive:
            cur[stamps[i]] = True
        n, after, st, _c = cv2.connectedComponentsWithStats((~cur).astype(np.uint8), connectivity=4)
        inscribed = cv2.distanceTransform((~cur).astype(np.uint8), cv2.DIST_L2, 3)
        rmax = np.asarray(ndi.maximum(inscribed, after, index=np.arange(n)))
        # cells (before) that came apart into two or more pieces of some size
        pair = (before > 0) & (after > 0)
        key = np.unique(before[pair].astype(np.int64) * n + after[pair])
        pieces_of: dict[int, list[int]] = {}
        for k in key.tolist():
            pieces_of.setdefault(k // n, []).append(k % n)
        bad = np.zeros(n, bool)
        good = np.zeros(n, bool)
        for _cell, pieces in pieces_of.items():
            big = [q for q in pieces if st[q, cv2.CC_STAT_AREA] >= speck]
            if len(big) < 2:
                continue
            for q in big:
                if st[q, cv2.CC_STAT_AREA] >= min_area and rmax[q] >= min_r:
                    good[q] = True
                else:
                    bad[q] = True
        keep = []
        for i in alive:
            ys, xs = stamps[i]
            y0, y1 = max(0, ys.min() - 2), min(h, ys.max() + 3)
            x0, x1 = max(0, xs.min() - 2), min(w, xs.max() + 3)
            touch = np.unique(after[y0:y1, x0:x1])
            if bad[touch].any() or not good[touch].any():
                continue
            keep.append(i)
        if keep == alive:
            break
        alive = keep
    out = mask.copy()
    for i in alive:
        out[stamps[i]] = True
    return out, len(alive)
