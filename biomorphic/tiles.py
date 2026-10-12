"""Tile helpers shared by the pipeline, the library and verify: cut a region out as RGBA, PNG encode/decode, hashing."""
from __future__ import annotations

import hashlib

import cv2
import numpy as np


def region_bbox(label_map: np.ndarray, region_id: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of one region in a label map. Raises ValueError if the region is empty."""
    ys, xs = np.nonzero(label_map == region_id)
    if ys.size == 0:
        raise ValueError(f"region {region_id} is empty")
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    return x0, y0, x1 - x0, y1 - y0


def cut_tile(rgb: np.ndarray, label_map: np.ndarray, region_id: int,
             bbox: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Cut one region out of the working copy.

    Returns (rgba, bbox). rgba is HxWx4 uint8; alpha is binary (255 inside the region, 0 elsewhere),
    so re-compositing every tile of a level reproduces the working copy inside the figure mask exactly.
    """
    if bbox is None:
        bbox = region_bbox(label_map, region_id)
    x, y, w, h = bbox
    crop = rgb[y:y + h, x:x + w]
    alpha = (label_map[y:y + h, x:x + w] == region_id).astype(np.uint8) * 255
    rgba = np.dstack([crop, alpha])
    return np.ascontiguousarray(rgba), bbox


def encode_png(rgba: np.ndarray) -> bytes:
    """Lossless PNG bytes for an RGBA (or RGB) uint8 array."""
    if rgba.ndim == 3 and rgba.shape[2] == 4:
        bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    elif rgba.ndim == 3 and rgba.shape[2] == 3:
        bgra = cv2.cvtColor(rgba, cv2.COLOR_RGB2BGR)
    else:
        bgra = rgba
    ok, buf = cv2.imencode(".png", bgra, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


def decode_png(data: bytes) -> np.ndarray:
    """RGBA uint8 array from PNG bytes (RGB input gets an opaque alpha)."""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError("not a decodable image")
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGRA)
    elif arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2BGRA)
    return cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
