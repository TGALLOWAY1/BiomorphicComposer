"""View-time render modes for stored tiles (docs/ARCHITECTURE.md, section Library).

Tiles are stored once, as original colour + alpha (docs/V1_BRIEF.md, Non-negotiables). Everything here derives a
*view* of that stored array and nothing more: these are pure functions, nothing reads or writes disk, and the input
array is never modified.

  original   a copy of the tile
  greyscale  RGB replaced by an L*-based grey, alpha unchanged
  outline    only ink-like pixels (dark AND colourless) survive, as black with alpha = tile alpha x inkness,
             so a tile becomes just its drawn lines
  remap      every pixel's Lab shifted by (to - from) of the palette entry whose `from` is nearest the tile's
             mean Lab, clipped to the sRGB gamut, alpha unchanged
"""
from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from biomorphic.session import rgb_to_lab

MODES = ("original", "greyscale", "outline", "remap")

# "Ink-like" is a soft threshold, not a hard cut: each ramp is 1 at or below its first value, 0 at or above its
# second and a smoothstep in between. Centred on L* 45 and chroma 18, which separate black pen (L < 15, chroma ~0)
# from navy pencil (dark, but chroma around 30) and coloured marker on the sample drawings.
OUTLINE_L_RAMP = (35.0, 55.0)
OUTLINE_CHROMA_RAMP = (10.0, 26.0)

Palette = Sequence[dict]   # [{"from": [L, a, b], "to": [L, a, b]}, ...]

__all__ = ["MODES", "OUTLINE_L_RAMP", "OUTLINE_CHROMA_RAMP", "render", "mean_lab", "inkness", "grey_from_L",
           "nearest_palette_entry"]


# ----------------------------------------------------------------------------- helpers
def _as_rgba(rgba: np.ndarray) -> np.ndarray:
    """Validate the input. Returns an HxWx4 uint8 array that callers only read (RGB input gets an opaque alpha)."""
    a = np.asarray(rgba)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError("render expects an HxWx4 (or HxWx3) uint8 array")
    if a.shape[2] == 3:
        a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    return a


def _lab(rgba: np.ndarray) -> np.ndarray:
    """float32 HxWx3 CIE Lab (L 0..100, a/b centred on 0) of the colour channels; always a new array."""
    return rgb_to_lab(np.ascontiguousarray(rgba[..., :3]))


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """float32 Lab -> uint8 RGB, clipped to the sRGB gamut."""
    lab = np.ascontiguousarray(lab, dtype=np.float32)
    rgb = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


def _ramp_down(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """1 at or below lo, 0 at or above hi, smoothstep in between."""
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def grey_from_L(L: np.ndarray) -> np.ndarray:
    """CIE L* (0..100) -> sRGB grey level (uint8). The same mapping as Lab (L, 0, 0) -> sRGB, but exact: R == G == B."""
    fy = (np.clip(np.asarray(L, np.float64), 0.0, 100.0) + 16.0) / 116.0
    y = np.where(fy > 6.0 / 29.0, fy ** 3, 3.0 * (6.0 / 29.0) ** 2 * (fy - 4.0 / 29.0))
    y = np.clip(y, 0.0, 1.0)
    v = np.where(y <= 0.0031308, 12.92 * y, 1.055 * np.power(y, 1.0 / 2.4) - 0.055)
    return np.clip(np.rint(v * 255.0), 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------- public pure functions
def mean_lab(rgba: np.ndarray) -> tuple[float, float, float]:
    """Mean CIE Lab (L 0..100, a/b centred on 0) over the pixels with alpha > 0. (0, 0, 0) for an empty tile."""
    a = _as_rgba(rgba)
    mask = a[..., 3] > 0
    if not mask.any():
        return (0.0, 0.0, 0.0)
    px = a[..., :3][mask].reshape(-1, 1, 3)              # fancy indexing copies; the input is untouched
    m = rgb_to_lab(px).reshape(-1, 3).astype(np.float64).mean(axis=0)
    return (float(m[0]), float(m[1]), float(m[2]))


def inkness(rgba: np.ndarray) -> np.ndarray:
    """Per-pixel 0..1 'how much like black ink' (dark AND colourless), float32 HxW. Alpha is ignored here."""
    lab = _lab(_as_rgba(rgba))
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    dark = _ramp_down(lab[..., 0], *OUTLINE_L_RAMP)
    colourless = _ramp_down(chroma, *OUTLINE_CHROMA_RAMP)
    return (dark * colourless).astype(np.float32)


def nearest_palette_entry(lab: Sequence[float], palette: Palette) -> int:
    """Index of the palette entry whose `from` colour is nearest (Euclidean Lab) to `lab`."""
    entries = _parse_palette(palette)
    centre = np.asarray(lab, np.float64).reshape(3)
    dists = [float(np.linalg.norm(centre - frm)) for frm, _to in entries]
    return int(np.argmin(dists))


def render(rgba: np.ndarray, mode: str, palette: Palette | None = None) -> np.ndarray:
    """Render a stored tile (HxWx4 uint8, original colour + alpha) in one of MODES. Returns a new HxWx4 uint8 array.

    Raises ValueError for an unknown mode, for `remap` without a palette, or for a malformed palette.
    """
    src = _as_rgba(rgba)
    if mode == "original":
        return src.copy()
    if mode == "greyscale":
        return _greyscale(src)
    if mode == "outline":
        return _outline(src)
    if mode == "remap":
        if not palette:
            raise ValueError("remap needs a palette: [{'from': [L, a, b], 'to': [L, a, b]}, ...]")
        return _remap(src, palette)
    raise ValueError(f"unknown render mode {mode!r}; expected one of {MODES}")


# ----------------------------------------------------------------------------- modes
def _greyscale(src: np.ndarray) -> np.ndarray:
    grey = grey_from_L(_lab(src)[..., 0])
    out = np.empty_like(src)
    out[..., 0] = grey
    out[..., 1] = grey
    out[..., 2] = grey
    out[..., 3] = src[..., 3]
    return out


def _outline(src: np.ndarray) -> np.ndarray:
    ink = inkness(src)
    out = np.zeros_like(src)                               # black everywhere; only alpha carries the lines
    out[..., 3] = np.clip(np.rint(src[..., 3].astype(np.float32) * ink), 0, 255).astype(np.uint8)
    return out


def _parse_palette(palette: Palette) -> list[tuple[np.ndarray, np.ndarray]]:
    if palette is None or len(palette) == 0:
        raise ValueError("palette must be a non-empty list of {'from': [L, a, b], 'to': [L, a, b]} entries")
    entries = []
    for i, entry in enumerate(palette):
        try:
            frm = np.asarray(entry["from"], np.float64).reshape(3)
            to = np.asarray(entry["to"], np.float64).reshape(3)
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"palette entry {i} must have 'from' and 'to' as [L, a, b]") from e
        if not (np.all(np.isfinite(frm)) and np.all(np.isfinite(to))):
            raise ValueError(f"palette entry {i} has non-finite values")
        entries.append((frm, to))
    return entries


def _remap(src: np.ndarray, palette: Palette) -> np.ndarray:
    entries = _parse_palette(palette)
    frm, to = entries[nearest_palette_entry(mean_lab(src), palette)]
    delta = (to - frm).astype(np.float32)
    lab = _lab(src) + delta                                # broadcasting makes a new array
    lab[..., 0] = np.clip(lab[..., 0], 0.0, 100.0)
    out = np.empty_like(src)
    out[..., :3] = _lab_to_rgb(lab)                        # out-of-gamut colours are clipped to sRGB
    out[..., 3] = src[..., 3]
    return out
