"""Contact sheet PNG for the verify report (docs/V1_BRIEF.md, Verification / Visual).

Row 1: the source with 2-px region outlines at three levels (finest, picked, coarse ~15 regions).
Row 2: the axis overlay (line + mirrored-mask IoU), mirror pairs colour-coded at the picked level
       (same colour for both twins, grey = unpaired, dark hatch = on-axis), and, when ground truth is
       available, a grid of map piece | matched region | score.
Outlines are computed from the label map (never from the photo), 2 px wide, in a magenta that reads on
black ink, navy pencil, teal marker and beige paper alike. Every panel is at most ~900 px tall.
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from biomorphic.verify.checks import all_boundaries, mask_bbox, mask_iou, match_rows, reflect_mask

OUTLINE_RGB = (255, 40, 170)       # magenta
AXIS_RGB = (255, 150, 0)           # orange
BG_RGB = (28, 28, 30)
CAPTION_BG = (48, 48, 52)
TEXT_RGB = (240, 240, 240)
PANEL_MAX_H = 900
PANEL_MIN_H = 400
CAPTION_H = 36
PAD = 14
COARSE_TARGET = 15


# ----------------------------------------------------------------------------- scaling helpers
def panel_scale(h: int) -> float:
    """Scale that brings an image of height h into [PANEL_MIN_H, PANEL_MAX_H] (small fixtures are enlarged)."""
    if h > PANEL_MAX_H:
        return PANEL_MAX_H / h
    if h < PANEL_MIN_H:
        return PANEL_MIN_H / h
    return 1.0


def resize_rgb(rgb: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-9:
        return rgb.copy()
    h, w = rgb.shape[:2]
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST
    return cv2.resize(rgb, size, interpolation=interp)


def resize_labels(lab: np.ndarray, scale: float, size: tuple[int, int] | None = None) -> np.ndarray:
    if abs(scale - 1.0) < 1e-9 and size is None:
        return lab
    h, w = lab.shape[:2]
    size = size or (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return cv2.resize(lab.astype(np.int32), size, interpolation=cv2.INTER_NEAREST)


def draw_outlines(rgb: np.ndarray, lab_small: np.ndarray, colour: tuple[int, int, int] = OUTLINE_RGB) -> np.ndarray:
    out = rgb.copy()
    out[all_boundaries(lab_small)] = colour
    return out


def pair_colours(n: int) -> list[tuple[int, int, int]]:
    cols = []
    for i in range(max(1, n)):
        r, g, b = colorsys.hsv_to_rgb((i * 0.618033988749895) % 1.0, 0.70, 0.95)
        cols.append((int(r * 255), int(g * 255), int(b * 255)))
    return cols


def put_text(img: np.ndarray, text: str, org: tuple[int, int], scale: float = 0.6,
             colour: tuple[int, int, int] = TEXT_RGB, thickness: int = 1) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness, cv2.LINE_AA)


def text_width(text: str, scale: float = 0.6, thickness: int = 1) -> int:
    (w, _h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    return int(w)


# ----------------------------------------------------------------------------- panels
def coarse_level(session: Any, picked: int, target: int = COARSE_TARGET) -> int:
    """The level whose region count is closest to `target`, preferring one that is not the finest/picked."""
    levels = session.levels
    ranked = sorted(levels, key=lambda lv: (abs(int(lv["n_regions"]) - target), -int(lv["index"])))
    for lv in ranked:
        if int(lv["index"]) not in (0, picked):
            return int(lv["index"])
    return int(ranked[0]["index"])


def outlines_panel(session: Any, level: int, scale: float) -> tuple[np.ndarray, str]:
    base = resize_rgb(session.working_rgb, scale)
    lab = resize_labels(session.label_map(level), scale, (base.shape[1], base.shape[0]))
    n = len(session.regions(level))
    return draw_outlines(base, lab), f"level {level}: {n} regions, outlines from label map"


def axis_panel(session: Any, scale: float) -> tuple[np.ndarray, str]:
    base = resize_rgb(session.working_rgb, scale)
    h, w = base.shape[:2]
    fig = session.figure_mask
    axis_x = float(session.axis_x)
    mirrored = reflect_mask(fig, axis_x)
    iou_full = mask_iou(fig, mirrored)
    fig_s = cv2.resize(fig.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
    mir_s = cv2.resize(mirrored.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
    out = base.astype(np.float32)
    both = fig_s & mir_s
    only = fig_s ^ mir_s
    out[both] = out[both] * 0.65 + np.array([60, 200, 90], np.float32) * 0.35
    out[only] = out[only] * 0.5 + np.array([230, 40, 40], np.float32) * 0.5
    out = out.clip(0, 255).astype(np.uint8)
    xs = int(round(axis_x * scale))
    cv2.line(out, (xs, 0), (xs, h - 1), AXIS_RGB, 2, cv2.LINE_AA)
    meta_axis = session.meta.get("axis", {})
    cap = (f"axis x={axis_x:.1f}  score={_fmt(meta_axis.get('score'))}  "
           f"mirror IoU={_fmt(meta_axis.get('mirror_iou'))} (recomputed {iou_full:.3f})  "
           f"green=overlap red=asymmetric")
    return out, cap


def pairs_panel(session: Any, level: int, scale: float) -> tuple[np.ndarray, str]:
    base = resize_rgb(session.working_rgb, scale)
    h, w = base.shape[:2]
    lab = resize_labels(session.label_map(level), scale, (w, h))
    pairing = session.pairing(level)
    sides = pairing.get("sides", {})
    twin = pairing.get("twin", {})
    pairs = sorted(pairing.get("pairs", []), key=lambda p: -float(p.get("iou", 0)))
    colours = pair_colours(len(pairs))
    ids = [int(i) for i in np.unique(lab) if i != 0]
    # dense lookup tables (region ids can be large: group ids start at 1_000_000)
    id_index = {rid: k for k, rid in enumerate(ids)}
    dense = np.zeros(lab.shape, np.int32)
    for rid, k in id_index.items():
        dense[lab == rid] = k + 1
    lut = np.zeros((len(ids) + 1, 3), np.float32)
    kind = np.zeros(len(ids) + 1, np.int8)  # 0 bg, 1 paired, 2 unpaired, 3 axis
    pair_colour: dict[int, tuple[int, int, int]] = {}
    for k, p in enumerate(pairs):
        pair_colour[int(p["a"])] = colours[k]
        pair_colour[int(p["b"])] = colours[k]
    for rid, k in id_index.items():
        if sides.get(rid) == "axis":
            lut[k + 1] = (90, 90, 95)
            kind[k + 1] = 3
        elif rid in pair_colour:
            lut[k + 1] = pair_colour[rid]
            kind[k + 1] = 1
        else:
            lut[k + 1] = (165, 165, 165)
            kind[k + 1] = 2
    fill = lut[dense]
    kinds = kind[dense]
    out = base.astype(np.float32)
    m = kinds > 0
    out[m] = out[m] * 0.4 + fill[m] * 0.6
    yy, xx = np.mgrid[0:h, 0:w]
    hatch = (((xx + yy) // 7) % 2 == 0) & (kinds == 3)
    out[hatch] = out[hatch] * 0.45 + np.array([20, 20, 20], np.float32) * 0.55
    out = out.clip(0, 255).astype(np.uint8)
    out[all_boundaries(lab)] = (25, 25, 28)
    xs = int(round(float(session.axis_x) * scale))
    cv2.line(out, (xs, 0), (xs, h - 1), AXIS_RGB, 1, cv2.LINE_AA)
    n_axis = sum(1 for s in sides.values() if s == "axis")
    off_axis = [rid for rid in sides if sides[rid] != "axis"]
    unpaired = [rid for rid in off_axis if twin.get(rid) is None]
    cap = (f"level {level}: {len(pairs)} mirror pairs (same colour), {len(unpaired)} unpaired (grey), "
           f"{n_axis} on-axis (hatched)")
    return out, cap


# ----------------------------------------------------------------------------- ground-truth grid
def _gt_objects(gt: Any) -> dict | None:
    if gt is None:
        return None
    if isinstance(gt, dict):
        if "_objects" in gt:
            return gt["_objects"]
        return gt
    details = getattr(gt, "details", None)
    if isinstance(details, dict):
        return details.get("_objects")
    return None


def _piece_crop(map_rgb: np.ndarray, piece: Any) -> np.ndarray | None:
    """Crop of one map piece on white, from a full-frame or bbox-sized piece mask."""
    mask = np.asarray(getattr(piece, "mask", None))
    if mask is None or mask.ndim != 2:
        return None
    mask = mask.astype(bool)
    H, W = map_rgb.shape[:2]
    if mask.shape == (H, W):
        bb = mask_bbox(mask)
        if bb is None:
            return None
        x, y, w, h = bb
        crop = map_rgb[y:y + h, x:x + w].copy()
        m = mask[y:y + h, x:x + w]
    else:
        bbox = getattr(piece, "bbox", None)
        if bbox is None or len(bbox) != 4:
            return None
        x, y, w, h = (int(v) for v in bbox)
        if (h, w) != mask.shape:  # maybe (x0, y0, x1, y1)
            x0, y0, x1, y1 = (int(v) for v in bbox)
            x, y, w, h = x0, y0, x1 - x0, y1 - y0
        if (h, w) != mask.shape:
            return None
        crop = map_rgb[y:y + h, x:x + w].copy()
        m = mask
    crop[~m] = (255, 255, 255)
    return crop


def region_id_from(tile_id: Any) -> int | None:
    """Region id from a candidate id: an int, a numeric string, or a tile id ending in -r<id> / r<id>."""
    if tile_id is None:
        return None
    if isinstance(tile_id, (int, np.integer)):
        return int(tile_id)
    s = str(tile_id)
    if s.lstrip("-").isdigit():
        return int(s)
    m = re.search(r"r(\d+)$", s)
    return int(m.group(1)) if m else None


def _region_crop(session: Any, level: int, rid: Any) -> np.ndarray | None:
    rid = region_id_from(rid)
    if rid is None:
        return None
    try:
        rgba, _bbox = session.cut_tile(level, int(rid))
    except Exception:  # noqa: BLE001
        return None
    out = rgba[..., :3].copy()
    out[rgba[..., 3] == 0] = (255, 255, 255)
    return out


def _thumb(img: np.ndarray | None, size: int) -> np.ndarray:
    canvas = np.full((size, size, 3), 255, np.uint8)
    if img is None or img.size == 0:
        cv2.line(canvas, (6, 6), (size - 6, size - 6), (200, 60, 60), 2)
        cv2.line(canvas, (6, size - 6), (size - 6, 6), (200, 60, 60), 2)
        return canvas
    h, w = img.shape[:2]
    s = (size - 8) / max(h, w)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_NEAREST)
    ox, oy = (size - nw) // 2, (size - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = small
    return canvas


def gt_panel(session: Any, gt: Any, thumb: int = 96, per_column: int = 6) -> tuple[np.ndarray, str]:
    objs = _gt_objects(gt)
    if not objs:
        raise ValueError("no ground-truth objects available")
    pieces = list(objs["pieces"])
    table = objs["table"]
    map_rgb = objs["map_rgb"]
    level = int(objs.get("level", 0))
    rows = match_rows(table)
    by_piece = {r["piece_index"]: r for r in rows if r.get("piece_index") is not None}
    by_label = {str(r["label"]): r for r in rows if r.get("label") is not None}
    # labelled pieces first, in label order, so the grid reads like the map's grid
    pieces = sorted(pieces, key=lambda p: (getattr(p, "label", None) is None, str(getattr(p, "label", "")),
                                           int(getattr(p, "index", 0))))
    cell_w, cell_h = 2 * thumb + 24, thumb + 40
    n = len(pieces)
    n_cols = max(1, int(np.ceil(n / per_column)))
    width, height = n_cols * cell_w + 8, min(n, per_column) * cell_h + 8
    out = np.full((max(height, 60), max(width, 320), 3), 250, np.uint8)
    matched = 0
    for k, piece in enumerate(pieces):
        col, row = divmod(k, per_column)
        x0, y0 = 4 + col * cell_w, 4 + row * cell_h
        plabel = getattr(piece, "label", None)
        pindex = getattr(piece, "index", None)
        label = str(plabel) if plabel is not None else f"#{pindex if pindex is not None else k}"
        r = by_piece.get(pindex) if pindex is not None else None
        if r is None:
            r = by_label.get(label)
        if r is None and k < len(rows) and not by_piece and not by_label:
            r = rows[k]
        tile_id = r["tile_id"] if r else None
        score = r["score"] if r else None
        ok = bool(r["matched"]) if r else False
        matched += int(ok)
        head = (70, 170, 90) if ok else (205, 70, 70)
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + cell_h - 4), head, 2)
        cv2.rectangle(out, (x0, y0), (x0 + cell_w - 4, y0 + 22), head, -1)
        put_text(out, f"{label} -> {'r' + str(tile_id) if tile_id is not None else '-'}   "
                      f"{'%.2f' % score if score is not None else 'n/a'} {'ok' if ok else 'MISS'}",
                 (x0 + 6, y0 + 16), 0.45, (255, 255, 255), 1)
        out[y0 + 28:y0 + 28 + thumb, x0 + 6:x0 + 6 + thumb] = _thumb(_piece_crop(map_rgb, piece), thumb)
        reg = _region_crop(session, level, tile_id) if tile_id is not None else None
        out[y0 + 28:y0 + 28 + thumb, x0 + 12 + thumb:x0 + 12 + 2 * thumb] = _thumb(reg, thumb)
    cap = f"ground truth: {matched}/{n} map pieces matched at level {level} (map piece | matched region | score)"
    return out, cap


def unavailable_panel(text: str, width: int = 600, height: int = 400) -> tuple[np.ndarray, str]:
    out = np.full((height, width, 3), 60, np.uint8)
    y = 40
    for line in _wrap(text, 70):
        put_text(out, line, (16, y), 0.55, (230, 200, 120), 1)
        y += 26
    return out, "panel unavailable"


def _wrap(text: str, n: int) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


# ----------------------------------------------------------------------------- composition
def compose(rows: list[list[tuple[np.ndarray, str]]], title: str | None = None) -> np.ndarray:
    """Lay panels (image, caption) out in rows; each panel gets a caption strip above it."""
    built_rows = []
    for row in rows:
        panels = []
        for img, cap in row:
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
            w = max(img.shape[1], text_width(cap, 0.55) + 16)
            strip = np.full((CAPTION_H, w, 3), CAPTION_BG, np.uint8)
            put_text(strip, cap, (8, CAPTION_H - 12), 0.55, TEXT_RGB, 1)
            body = np.full((img.shape[0], w, 3), BG_RGB, np.uint8)
            body[:, :img.shape[1]] = img
            panels.append(np.vstack([strip, body]))
        built_rows.append(panels)
    row_h = [max(p.shape[0] for p in r) for r in built_rows]
    row_w = [sum(p.shape[1] for p in r) + PAD * (len(r) + 1) for r in built_rows]
    title_h = 56 if title else 0
    W = max(row_w)
    H = title_h + sum(row_h) + PAD * (len(built_rows) + 1)
    sheet = np.full((H, W, 3), BG_RGB, np.uint8)
    if title:
        put_text(sheet, title, (PAD, 38), 0.9, TEXT_RGB, 2)
    y = title_h + PAD
    for panels, h in zip(built_rows, row_h):
        x = PAD
        for p in panels:
            sheet[y:y + p.shape[0], x:x + p.shape[1]] = p
            x += p.shape[1] + PAD
        y += h + PAD
    return sheet


def write_contact_sheet(session: Any, picked_level: int, out_path: Path | str, gt: Any = None,
                        title: str | None = None) -> Path:
    """Write the contact sheet PNG and return its path. The ground-truth panel is optional and degrades to a
    text panel when `gt` cannot be rendered; everything else is drawn from the session alone."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    picked = int(picked_level)
    scale = panel_scale(session.height)
    finest = 0
    coarse = coarse_level(session, picked)
    levels = [finest]
    if picked not in levels:
        levels.append(picked)
    if coarse not in levels:
        levels.append(coarse)
    row1 = [outlines_panel(session, lv, scale) for lv in levels]
    row2 = [axis_panel(session, scale), pairs_panel(session, picked, scale)]
    if gt is not None:
        try:
            row2.append(gt_panel(session, gt))
        except Exception as exc:  # noqa: BLE001
            row2.append(unavailable_panel(f"ground-truth panel unavailable: {type(exc).__name__}: {exc}"))
    src = session.meta.get("source", {}).get("filename", "")
    title = title or f"Biomorphic Composer verify - {src} - session {session.session_id} - picked level {picked}"
    sheet = compose([row1, row2], title)
    ok = cv2.imwrite(str(out_path), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError(f"could not write {out_path}")
    return out_path


def _fmt(v: Any, nd: int = 3) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"
