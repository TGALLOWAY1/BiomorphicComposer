"""ink step: the black-ink mask. Ink = dark AND colourless in CIE Lab, both measured relative to the paper.

Why "relative to the paper": the photo's white balance leaves the paper with a warm tint (on the sample
L~86, a~4, b~16). A colourless surface photographed under the same light keeps that tint scaled down with
its lightness ((L+16)/(L_paper+16), the cube-root law of Lab), so the darkest ink lands near (a, b) ~ (1, 1),
not near the paper's (4, 16) and not exactly at 0. Measuring chroma as the distance from the scaled paper
tint keeps ink inside the threshold while navy pencil (b ~ -16 at the same lightness) stays well outside.
Erring toward *not* calling dark colour ink is deliberate: the prototypes' worst failure was navy pencil
and colour gradients becoming boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from skimage.morphology import skeletonize

DEFAULTS = {
    "ink_max_L_frac": 0.41,     # dark: L < this fraction of the paper's L (0.41 * 86 = 35 on the sample)
    "ink_max_chroma": 14.0,     # colourless: (a, b) within this distance of the paper tint scaled to the pixel's L
    "ink_speck_area": 24,       # ink components smaller than this (px) are specks, not ink
    "ink_close_px": 1,          # close gaps up to this size (px) in the ink mask
}


@dataclass
class InkResult:
    mask: np.ndarray        # bool HxW
    dist: np.ndarray        # float32 HxW: distance to the nearest non-ink pixel inside ink (0 outside)
    t0: float               # median ink half-thickness (px) measured on the ink skeleton
    raw_fraction: float     # fraction of pixels classified as ink before cleaning (diagnostic)


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


def detect_ink(lab: np.ndarray, paper: np.ndarray, params: dict | None = None) -> InkResult:
    p = {**DEFAULTS, **(params or {})}
    raw = classify_ink(lab, paper, float(p["ink_max_L_frac"]), float(p["ink_max_chroma"]))
    mask = clean_mask(raw, int(p["ink_speck_area"]), int(p["ink_close_px"]))
    dist = ink_distance(mask)
    t0 = half_thickness(mask, dist)
    return InkResult(mask=mask, dist=dist, t0=t0, raw_fraction=float(raw.mean()))
