"""Pipeline orchestration: run every step and write a session directory (docs/ARCHITECTURE.md).

Order: ingest -> ink -> axis (-> deskew: rotate, recompute masks and axis) -> oversegment -> merge -> levels
-> write session (-> debug images). Every threshold used lands in session.json["params"].
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from biomorphic.pipeline import axis as axis_mod
from biomorphic.pipeline import ingest as ingest_mod
from biomorphic.pipeline import ink as ink_mod
from biomorphic.pipeline import merge as merge_mod
from biomorphic.pipeline import oversegment as overseg_mod
from biomorphic.pipeline import pairing as pairing_mod
from biomorphic.session import make_session_id, write_session
from biomorphic.version import PIPELINE_VERSION


def default_params() -> dict:
    """Every pipeline parameter with its default (the union of the step modules' DEFAULTS)."""
    out: dict = {}
    for mod in (ingest_mod, ink_mod, axis_mod, overseg_mod, merge_mod, pairing_mod):
        out.update(mod.DEFAULTS)
    return out


def _jsonable(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def run_pipeline(photo: Path | str, out_dir: Path | str, params: dict | None = None, debug: bool = False) -> Path:
    p = default_params()
    p.update(params or {})
    timings: dict[str, float] = {}
    t = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[name] = round(now - t, 3)
        t = now

    source = ingest_mod.load_oriented(photo)
    ing = ingest_mod.ingest(source, p)
    working = ing.working_rgb
    lab, figure, figure_box = ing.working_lab, ing.figure_mask, ing.figure_box
    lap("ingest")

    ink = ink_mod.detect_ink(lab, ing.paper_lab, p)
    lap("ink")

    ax = axis_mod.find_axis(figure, p)
    deskew = 0.0
    if abs(ax["angle_deg"]) > float(p["deskew_min_angle"]):
        deskew = float(ax["angle_deg"])
        working = ingest_mod.rotate_rgb(working, deskew, ing.paper_rgb)
        lab, figure, figure_box = ingest_mod.analyse_working(working, ing.paper_lab, p)
        ink = ink_mod.detect_ink(lab, ing.paper_lab, p)
        ax = axis_mod.find_axis(figure, p, angles=[0.0])
    lap("axis")

    seg = overseg_mod.oversegment(figure, ink.mask, ink.dist, p)
    lap("oversegment")

    mr = merge_mod.build_merge_tree(seg.fragments, ink.dist, ink.t0, ax["x"], p)
    levels = merge_mod.choose_levels(mr.tree, mr.stats, p)
    lap("merge")

    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    h, w = working.shape[:2]
    meta = {
        "pipeline_version": PIPELINE_VERSION,
        "session_id": make_session_id(source.sha256, PIPELINE_VERSION, created),
        "created_at": created,
        "source": {"path": str(photo), "filename": Path(photo).name, "sha256": source.sha256,
                   "width": int(source.rgb.shape[1]), "height": int(source.rgb.shape[0]),
                   "exif_orientation": int(source.exif_orientation)},
        "crop": _jsonable(ing.crop),
        "working": {"width": int(w), "height": int(h), "scale": float(ing.scale), "angle_deg": float(deskew)},
        "axis": {"x": float(ax["x"]), "score": float(ax["score"]), "mirror_iou": float(ax["mirror_iou"]),
                 "angle_deg": float(ax["angle_deg"]), "search": _jsonable(ax["search"])},
        "params": _jsonable(p),
        "counts": {"fragments": int(seg.n_leaves), "cells": int(seg.n_cells), "markers": int(seg.n_markers),
                   "stragglers": int(seg.n_stragglers), "merges": len(mr.tree.merges),
                   "disconnected_merges": int(mr.n_disconnected)},
        "levels": levels,
        "measured": {"paper_lab": [round(float(v), 2) for v in ing.paper_lab],
                     "paper_rgb": [int(v) for v in ing.paper_rgb],
                     "ink_t0": round(float(ink.t0), 3), "ink_fraction": round(float(ink.mask.mean()), 5),
                     "ink_raw_fraction": round(float(ink.raw_fraction), 5),
                     "figure_box_working": [int(v) for v in figure_box],
                     "figure_area": int(figure.sum())},
    }
    out = write_session(out_dir, meta=meta, working_rgb=working, figure_mask=figure, ink_mask=ink.mask,
                        fragments=seg.fragments, tree=mr.tree)
    lap("write")
    meta["timings"] = timings
    (out / "session.json").write_text(json.dumps({**meta, "schema": 1}, indent=1))

    if debug:
        from biomorphic.pipeline.debug import write_debug_images
        dbg = out / "debug"
        dbg.mkdir(exist_ok=True)
        (dbg / "merge_stats.json").write_text(json.dumps(mr.stats))
        write_debug_images(out)
        lap("debug")
        meta["timings"] = timings
        (out / "session.json").write_text(json.dumps({**meta, "schema": 1}, indent=1))
    return out
