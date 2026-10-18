"""ingest step: EXIF orientation, paper estimate, figure mask and box, crop with margin, working copy, deskew.

Every function is a pure function on arrays. `run.py` wires them: the oriented source is searched for the
figure box at a reduced width, cropped with a margin at full resolution, downscaled to the working width
(never upscaled) and the figure mask is recomputed on the working copy. The optional deskew rotation
(`rotate_rgb`) is applied by `run.py` after the axis search, followed by a recomputation of the masks.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi

from biomorphic.session import rgb_to_lab

DEFAULTS = {
    "working_width": 1800,         # max width of the working copy (px); the source is never upscaled
    "crop_margin_frac": 0.03,      # crop margin around the figure box, as a fraction of max(box w, box h)
    "paper_border_frac": 0.05,     # width of the image border band (fraction of w/h) used to estimate the paper
    "figure_dark_dL": 35.0,        # a pixel darker than the paper by this much (Lab L) belongs to the figure;
                                   # 25 let a shadowed pencil grid on the board join the figure (fill-holes then
                                   # turned the enclosed wood squares into figure); the ink outline is ~70 darker
    "figure_chroma_delta": 12.0,   # ... or whose (a, b) is further than this from the paper's (a, b)
    "figure_close_px": 2,          # closing radius (px) applied before taking the largest component
    "figure_box_width": 1000,      # the oriented source is reduced to this width to find the figure box
    "deskew_min_angle": 0.3,       # deskew only if |axis angle| exceeds this (degrees)
}

EXIF_ORIENTATION_TAG = 274


@dataclass
class SourceImage:
    rgb: np.ndarray            # HxWx3 uint8, EXIF orientation applied
    path: Path
    sha256: str                # of the file bytes
    exif_orientation: int


def load_oriented(path: Path | str) -> SourceImage:
    """Read a photo, apply its EXIF orientation and hash the file bytes."""
    path = Path(path)
    data = path.read_bytes()
    im = Image.open(io.BytesIO(data))
    try:
        orientation = int(im.getexif().get(EXIF_ORIENTATION_TAG, 1) or 1)
    except Exception:  # pragma: no cover - exotic files without a usable EXIF block
        orientation = 1
    im = ImageOps.exif_transpose(im)
    rgb = np.ascontiguousarray(np.asarray(im.convert("RGB")))
    return SourceImage(rgb=rgb, path=path, sha256=hashlib.sha256(data).hexdigest(), exif_orientation=orientation)


def border_band(shape: tuple[int, ...], frac: float) -> np.ndarray:
    """Boolean mask of the outer band of an image (used for the paper estimate)."""
    h, w = shape[:2]
    bh, bw = max(1, int(round(h * frac))), max(1, int(round(w * frac)))
    band = np.zeros((h, w), bool)
    band[:bh, :] = True
    band[-bh:, :] = True
    band[:, :bw] = True
    band[:, -bw:] = True
    return band


def estimate_paper(lab: np.ndarray, border_frac: float) -> np.ndarray:
    """Median Lab of the border band: robust to the pencil grid, shadows and the figure touching the edge."""
    band = border_band(lab.shape, border_frac)
    return np.median(lab[band].reshape(-1, 3), axis=0).astype(np.float32)


def estimate_paper_rgb(rgb: np.ndarray, border_frac: float) -> tuple[int, int, int]:
    band = border_band(rgb.shape, border_frac)
    med = np.median(rgb[band].reshape(-1, 3), axis=0)
    return tuple(int(round(v)) for v in med)


def figure_candidates(lab: np.ndarray, paper: np.ndarray, dark_dL: float, chroma_delta: float) -> np.ndarray:
    """Pixels that are darker than the paper or clearly more chromatic than it."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    dark = L < float(paper[0]) - dark_dL
    chroma = np.hypot(a - float(paper[1]), b - float(paper[2])) > chroma_delta
    return dark | chroma


def largest_component(mask: np.ndarray, connectivity: int = 4) -> np.ndarray:
    m = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=connectivity)
    if n <= 1:
        return np.zeros(mask.shape, bool)
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == big


def figure_mask_from_lab(lab: np.ndarray, paper: np.ndarray, *, dark_dL: float, chroma_delta: float,
                         close_px: int) -> np.ndarray:
    """Figure silhouette: largest 4-connected component of the candidates after a small closing, holes filled."""
    cand = figure_candidates(lab, paper, dark_dL, chroma_delta).astype(np.uint8)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * close_px + 1, 2 * close_px + 1))
        cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, k)
    fig = largest_component(cand > 0, connectivity=4)
    return ndi.binary_fill_holes(fig)


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        raise ValueError("empty mask: no figure found")
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def find_figure_box(rgb: np.ndarray, params: dict | None = None) -> tuple[tuple[int, int, int, int], np.ndarray]:
    """Figure bbox (x, y, w, h) in the coordinates of `rgb`, found on a reduced copy. Also returns the paper Lab."""
    p = {**DEFAULTS, **(params or {})}
    h, w = rgb.shape[:2]
    s = min(1.0, p["figure_box_width"] / w)
    small = cv2.resize(rgb, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    lab = rgb_to_lab(small)
    paper = estimate_paper(lab, p["paper_border_frac"])
    mask = figure_mask_from_lab(lab, paper, dark_dL=p["figure_dark_dL"], chroma_delta=p["figure_chroma_delta"],
                                close_px=p["figure_close_px"])
    x, y, bw, bh = mask_bbox(mask)
    inv = 1.0 / s
    x0, y0 = int(np.floor(x * inv)), int(np.floor(y * inv))
    x1, y1 = int(np.ceil((x + bw) * inv)), int(np.ceil((y + bh) * inv))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    return (x0, y0, x1 - x0, y1 - y0), paper


def crop_with_margin(rgb: np.ndarray, box: tuple[int, int, int, int], margin_frac: float) -> tuple[np.ndarray, dict]:
    """Crop the figure box plus a margin (clamped to the image). Returns the crop and its record."""
    h, w = rgb.shape[:2]
    x, y, bw, bh = box
    margin = int(round(margin_frac * max(bw, bh)))
    x0, y0 = max(0, x - margin), max(0, y - margin)
    x1, y1 = min(w, x + bw + margin), min(h, y + bh + margin)
    crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    rec = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "margin_px": margin,
           "figure_box": {"x": x, "y": y, "w": bw, "h": bh}}
    return crop, rec


def make_working(crop: np.ndarray, working_width: int) -> tuple[np.ndarray, float]:
    """Downscale so that width <= working_width (never upscale). Returns (working rgb, scale)."""
    h, w = crop.shape[:2]
    if w <= working_width:
        return crop, 1.0
    scale = working_width / w
    out = cv2.resize(crop, (working_width, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(out), scale


def rotate_rgb(rgb: np.ndarray, angle_deg: float, fill: tuple[int, int, int]) -> np.ndarray:
    """Rotate about the image centre (cv2 convention: positive = counter-clockwise), same size, paper fill."""
    h, w = rgb.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), float(angle_deg), 1.0)
    return cv2.warpAffine(rgb, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=tuple(int(v) for v in fill))


@dataclass
class Ingested:
    source: SourceImage
    paper_lab: np.ndarray          # (3,) float32, from the oriented source's border band
    paper_rgb: tuple[int, int, int]
    crop: dict                     # oriented-source coords
    working_rgb: np.ndarray
    working_lab: np.ndarray
    scale: float                   # working px per source px
    figure_mask: np.ndarray        # bool, working px
    figure_box: tuple[int, int, int, int]   # working px


def analyse_working(working_rgb: np.ndarray, paper_lab: np.ndarray, params: dict) -> tuple[np.ndarray, np.ndarray, tuple]:
    """Lab, figure mask and figure box of a working copy."""
    p = {**DEFAULTS, **(params or {})}
    lab = rgb_to_lab(working_rgb)
    mask = figure_mask_from_lab(lab, paper_lab, dark_dL=p["figure_dark_dL"], chroma_delta=p["figure_chroma_delta"],
                                close_px=p["figure_close_px"])
    return lab, mask, mask_bbox(mask)


def ingest(source: SourceImage, params: dict | None = None) -> Ingested:
    p = {**DEFAULTS, **(params or {})}
    box, paper_lab = find_figure_box(source.rgb, p)
    paper_rgb = estimate_paper_rgb(source.rgb, p["paper_border_frac"])
    crop, crop_rec = crop_with_margin(source.rgb, box, p["crop_margin_frac"])
    working, scale = make_working(crop, int(p["working_width"]))
    lab, mask, wbox = analyse_working(working, paper_lab, p)
    return Ingested(source=source, paper_lab=paper_lab, paper_rgb=paper_rgb, crop=crop_rec, working_rgb=working,
                    working_lab=lab, scale=scale, figure_mask=mask, figure_box=wbox)
