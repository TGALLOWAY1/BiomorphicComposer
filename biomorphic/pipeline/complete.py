"""complete step: recover faint ink lines with the help of the folded other half.

A line that photographed faintly (thin, pen running dry, blur) can drop out of the ink mask; the two cells it
separates then come out as one ("stitched"). The drawing is symmetric, so the fold shows it: the stitched cell
lies on top of two or more cells of the other half. For such a cell, the other half's dividing line is folded
onto it, and the faint dark pixels under that folded line (a thin dark structure, darker than the fills but
not dark enough for the ink mask) are added to the ink. The split is kept only when every piece it produces
lies on a different cell of the other half, so a line is only ever promoted where it was drawn on this side
too: nothing is copied over from the mirror image alone, and pencil shading beside a line does not qualify.
"""
from __future__ import annotations

import cv2
import numpy as np

from biomorphic.pipeline.ink import colourless_distance

DEFAULTS = {
    "complete_lines": True,          # recover faint lines where the fold shows a stitched cell
    "complete_min_share": 0.15,      # a counterpart cell must cover at least this share of the stitched cell
    "complete_faint_L_frac": 0.6,    # faint line: L below this fraction of the paper's L ...
    "complete_faint_contrast": 12.0,  # ... black-hat contrast above this ...
    "complete_faint_chroma": 40.0,   # ... relative chroma below this
    "complete_dilate_px": 3,         # tolerance around the folded line (working px)
    "complete_owner_share": 0.5,     # a piece belongs to the counterpart cell holding this share of its twins
    "complete_passes": 2,            # repeat once the first splits have sharpened the picture
}


def complete_lines(cells: np.ndarray, mirror_cells: np.ndarray, ink: np.ndarray, mirror_ink: np.ndarray,
                   lab: np.ndarray, paper: np.ndarray, t0: float, min_area: int,
                   params: dict | None = None) -> tuple[np.ndarray, list[dict]]:
    """Return (ink with recovered lines, one record per split cell).

    cells: first-pass leaf map (0 outside the figure); mirror_cells: the cell of every pixel's twin;
    mirror_ink: the other half's ink folded onto this half.
    """
    p = {**DEFAULTS, **(params or {})}
    if not p["complete_lines"]:
        return ink, []
    L = lab[..., 0]
    rel = colourless_distance(lab, paper)
    r = int(round(3 * t0)) + 2
    blackhat = cv2.morphologyEx(L.astype(np.float32), cv2.MORPH_BLACKHAT,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    faint = (L < float(p["complete_faint_L_frac"]) * float(paper[0])) & \
            (blackhat > float(p["complete_faint_contrast"])) & (rel < float(p["complete_faint_chroma"]))
    d = int(p["complete_dilate_px"])
    folded_line = cv2.dilate(mirror_ink.astype(np.uint8),
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))) > 0
    cand = faint & folded_line & ~ink & (cells > 0)
    out = ink.copy()
    records: list[dict] = []
    from scipy import ndimage as ndi
    objs = ndi.find_objects(cells)
    share = float(p["complete_min_share"])
    owner_share = float(p["complete_owner_share"])
    for cid, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        sub = cells[sl] == cid
        area = int(sub.sum())
        if area < 4 * min_area:
            continue
        twins, cnt = np.unique(mirror_cells[sl][sub], return_counts=True)
        sig = {int(t) for t, c in zip(twins, cnt) if t > 0 and t != cid and c >= share * area}
        if len(sig) < 2:
            continue
        add = cand[sl] & sub
        if not add.any():
            continue
        pieces_mask = (sub & ~add & ~ink[sl]).astype(np.uint8)
        n, lbl, st, _ = cv2.connectedComponentsWithStats(pieces_mask, connectivity=4)
        owners = []
        for k in range(1, n):
            if st[k, cv2.CC_STAT_AREA] < min_area:
                continue
            tw, tc = np.unique(mirror_cells[sl][lbl == k], return_counts=True)
            ok = (tw > 0) & (tw != cid)
            j = int(np.argmax(np.where(ok, tc, -1)))
            # the piece clearly lies on one counterpart cell, else it has no owner
            owners.append(int(tw[j]) if ok[j] and tc[j] >= owner_share * st[k, cv2.CC_STAT_AREA] else None)
        # at least two pieces, each on its own counterpart cell (a split the other half has too)
        if len(owners) >= 2 and None not in owners and len(set(owners)) == len(owners):
            out[sl] |= add
            records.append({"cell": cid, "area": area, "pieces": len(owners), "counterparts": sorted(owners),
                            "added_px": int(add.sum())})
    return out, records
