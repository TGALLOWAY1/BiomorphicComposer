"""A tiny synthetic session for tests and for developing the UI/library/verify without the real pipeline.

Five rectangular 'cells' with black ink borders on beige paper, mirror-symmetric about x = 120:
  1: left top (blue)   3: right top (blue, twin of 1)
  2: left bottom (green) 4: right bottom (green, twin of 2)
  5: on the axis (grey)
Merge tree: 1+2 -> 6, 3+4 -> 7, 6+7 -> 8, 8+5 -> 9. Levels: 5, 3, 2, 1 regions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from biomorphic.session import MergeTree, make_session_id, write_session
from biomorphic.version import PIPELINE_VERSION

AXIS_X = 120.0
W, H = 240, 200
INK = 4
RECTS = {  # id: (x0, y0, x1, y1, rgb)
    1: (20, 30, 70, 90, (70, 120, 190)),
    2: (20, 100, 70, 170, (120, 200, 160)),
    3: (170, 30, 220, 90, (70, 120, 190)),
    4: (170, 100, 220, 170, (120, 200, 160)),
    5: (95, 60, 145, 120, (160, 160, 165)),
}
PAPER = (235, 225, 205)


def synthetic_arrays():
    rgb = np.empty((H, W, 3), np.uint8)
    rgb[:] = PAPER
    fragments = np.zeros((H, W), np.int32)
    ink = np.zeros((H, W), bool)
    for rid, (x0, y0, x1, y1, col) in RECTS.items():
        rgb[y0:y1, x0:x1] = col
        fragments[y0:y1, x0:x1] = rid
        border = np.zeros((H, W), bool)
        border[y0:y1, x0:x1] = True
        border[y0 + INK:y1 - INK, x0 + INK:x1 - INK] = False
        rgb[border] = (20, 20, 20)
        ink |= border
    figure = fragments > 0
    tree = MergeTree(5, [[1, 2, 0.5, 6], [3, 4, 0.5, 7], [6, 7, 1.5, 8], [8, 5, 2.0, 9]])
    return rgb, figure, ink, fragments, tree


def synthetic_session(out_dir: Path | str) -> Path:
    rgb, figure, ink, fragments, tree = synthetic_arrays()
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_sha = "0" * 64
    meta = {
        "pipeline_version": PIPELINE_VERSION,
        "session_id": make_session_id(source_sha, PIPELINE_VERSION, created),
        "created_at": created,
        "source": {"path": "synthetic.png", "filename": "synthetic.png", "sha256": source_sha,
                   "width": W, "height": H, "exif_orientation": 1},
        "crop": {"x": 0, "y": 0, "w": W, "h": H, "margin_px": 0, "figure_box": {"x": 20, "y": 30, "w": 200, "h": 140}},
        "working": {"width": W, "height": H, "scale": 1.0, "angle_deg": 0.0},
        "axis": {"x": AXIS_X, "score": 1.0, "mirror_iou": 1.0, "angle_deg": 0.0,
                 "search": {"x_min": 60, "x_max": 180, "angles": [0.0]}},
        "params": {},
        "counts": {"fragments": 5, "cells": 5},
        "levels": [
            {"index": 0, "n_merges": 0, "n_regions": 5, "cost": 0.0},
            {"index": 1, "n_merges": 2, "n_regions": 3, "cost": 0.5},
            {"index": 2, "n_merges": 3, "n_regions": 2, "cost": 1.5},
            {"index": 3, "n_merges": 4, "n_regions": 1, "cost": 2.0},
        ],
    }
    return write_session(out_dir, meta=meta, working_rgb=rgb, figure_mask=figure, ink_mask=ink,
                         fragments=fragments, tree=tree)
