"""Fold, fold pairing, line recovery and mirror-consistent merging on small synthetic drawings."""
from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage as ndi

from biomorphic.pipeline import complete as complete_mod
from biomorphic.pipeline import fold as fold_mod
from biomorphic.pipeline import ink as ink_mod
from biomorphic.pipeline import merge as merge_mod
from biomorphic.pipeline import oversegment as overseg_mod
from biomorphic.pipeline import pairing as pairing_mod
from biomorphic.session import region_stats, rgb_to_lab

PAPER = (234, 211, 185)
INK = (25, 25, 28)
FILLS = [(230, 120, 60), (240, 200, 80), (200, 70, 70), (150, 110, 100), (250, 170, 90)]


def _drawing(w=240, h=200, axis=120, right_dy=0, right_dx=0, thick=3):
    """A mirror-symmetric drawing: two cells on each side and one central cell spanning the axis; the right
    half can be drawn a few px off (right_dx, right_dy), like a hand-drawn copy."""
    img = np.full((h, w, 3), PAPER, np.uint8)
    left_cells = [((30, 30), (axis - 30, 80)), ((30, 80), (axis - 30, 170))]
    for i, ((x0, y0), (x1, y1)) in enumerate(left_cells):
        col = FILLS[i]
        mx0, mx1 = 2 * axis - 1 - x1, 2 * axis - 1 - x0          # pixel x reflects to 2 * axis - 1 - x
        cv2.rectangle(img, (x0, y0), (x1, y1), col, -1)
        cv2.rectangle(img, (mx0 + right_dx, y0 + right_dy), (mx1 + right_dx, y1 + right_dy), col, -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), INK, thick)
        cv2.rectangle(img, (mx0 + right_dx, y0 + right_dy), (mx1 + right_dx, y1 + right_dy), INK, thick)
    cv2.rectangle(img, (axis - 30, 30), (axis + 29, 170), FILLS[2], -1)   # the central cell
    cv2.rectangle(img, (axis - 30, 30), (axis + 29, 170), INK, thick)
    return img


def _session_arrays(img, axis=120.0):
    lab = rgb_to_lab(img)
    paper = rgb_to_lab(np.full((2, 2, 3), PAPER, np.uint8))[0, 0]
    ink = ink_mod.detect_ink(lab, paper)
    figure = ndi.binary_fill_holes(np.any(img != np.array(PAPER, np.uint8), axis=-1))   # the drawing's silhouette
    return lab, paper, ink, figure


# ----------------------------------------------------------------------------- ink refinements
def test_colour_bled_thin_stroke_is_ink_but_navy_pencil_is_not():
    paper = rgb_to_lab(np.full((2, 2, 3), PAPER, np.uint8))[0, 0]
    img = np.full((80, 160, 3), (250, 140, 40), np.uint8)       # saturated orange fill
    img[38:42, 10:150] = (75, 45, 25)                           # thin black stroke that picked up the orange
    navy = img.copy()
    navy[:] = (170, 200, 220)                                   # pale blue fill ...
    navy[38:42, 10:150] = (40, 55, 110)                         # ... shaded with a navy pencil stroke
    m = ink_mod.detect_ink(rgb_to_lab(img), paper).mask
    assert m[39:41, 20:140].mean() > 0.95
    old = ink_mod.detect_ink(rgb_to_lab(img), paper, {"ink_rel_chroma_frac": 0, "ink_thin_contrast": 0}).mask
    assert old[39:41, 20:140].mean() < 0.5                      # the old rule lost the bled stroke
    assert not ink_mod.detect_ink(rgb_to_lab(navy), paper).mask[30:50, 10:150].any()


def test_wide_dark_fill_is_not_ink_but_keeps_its_rim():
    paper = rgb_to_lab(np.full((2, 2, 3), PAPER, np.uint8))[0, 0]
    img = np.full((140, 140, 3), (240, 180, 90), np.uint8)
    cv2.rectangle(img, (30, 30), (110, 110), (40, 28, 34), -1)   # deep plum fill, as dark as the ink
    cv2.rectangle(img, (30, 30), (110, 110), INK, 4)             # its black outline
    for x in range(10, 130, 30):                                 # some ordinary 4 px lines set the stroke width
        img[:, x:x + 4] = INK
    m = ink_mod.detect_ink(rgb_to_lab(img), paper).mask
    assert not m[55:85, 55:85].any()                             # the fill's inside is a cell
    _n, lbl = cv2.connectedComponents((~m).astype(np.uint8), connectivity=4)
    assert lbl[70, 70] != 0 and lbl[70, 70] != lbl[70, 20]       # but its outline still walls it off


def test_bridge_closes_a_short_gap_but_not_across_a_pale_fill():
    paper = rgb_to_lab(np.full((2, 2, 3), PAPER, np.uint8))[0, 0]
    img = np.full((120, 200, 3), (240, 170, 80), np.uint8)
    img[:, 98:102] = INK                                         # vertical line: two cells left and right
    img[58:62, 0:98] = INK                                       # left: a horizontal line across the cell ...
    img[58:62, 50:56] = (120, 90, 60)                            # ... with a faint 6 px stretch (pen running dry)
    img[58:62, 102:200] = INK                                    # right: a line with a real gap (fill shows through)
    img[58:62, 140:150] = (240, 170, 80)
    lab = rgb_to_lab(img)
    res = ink_mod.detect_ink(lab, paper)
    assert not res.mask[59:61, 50:56].all()                      # the faint stretch is not ink by itself
    out, n = ink_mod.bridge_gaps(res.mask, lab, paper, res.t0)
    assert n >= 1 and out[59:61, 50:56].all()                    # bridged: the left cell splits in two
    assert not out[59:61, 142:148].any()                         # a gap over pale fill is left open


def test_bridges_that_only_chip_a_scrap_are_undone():
    mask = np.zeros((100, 100), bool)
    mask[:, 48:52] = True
    seg = [((52, 50), (60, 50))]                                 # a stub that splits nothing
    out, n = ink_mod.keep_completing_bridges(mask, seg, 2, 2.0, 24)
    assert n == 0 and np.array_equal(out, mask)


# ----------------------------------------------------------------------------- fold
def test_fold_maps_each_point_onto_its_twin_despite_a_drawn_offset():
    img = _drawing(right_dy=4, right_dx=3)
    lab, paper, ink, figure = _session_arrays(img)
    fold = fold_mod.estimate_fold(lab, ink.mask, figure, 120.0, ink.t0)
    tx, ty = fold.twin_maps()
    # the left top cell's corner at (30, 30) folds onto the right top cell's corner (209 + 3, 30 + 4)
    assert abs(tx[30, 30] - (2 * 120 - 1 - 30 + 3)) <= 2.5 and abs(ty[30, 30] - 34) <= 2.5
    assert fold.meta["ink_on_folded_ink"]["fold"] > fold.meta["ink_on_folded_ink"]["plain"]
    curve = np.array(fold.meta["axis_curve"])
    assert np.all(np.abs(curve[:, 1] - 121.5) < 4)               # the local centre line sits between the halves


def test_fold_round_trips_through_the_session_file(tmp_path):
    img = _drawing()
    lab, paper, ink, figure = _session_arrays(img)
    fold = fold_mod.estimate_fold(lab, ink.mask, figure, 120.0, ink.t0)
    fold.save(tmp_path)
    back = fold_mod.Fold.load(tmp_path)
    assert back is not None and back.shape == fold.shape
    assert np.allclose(back.twin_maps()[0], fold.twin_maps()[0], atol=1e-3)
    assert fold_mod.Fold.load(tmp_path / "missing") is None


def test_fold_pairing_pairs_twins_and_finds_the_central_cell():
    img = _drawing(right_dy=3)
    lab, paper, ink, figure = _session_arrays(img)
    seg = overseg_mod.oversegment(figure, ink.mask, ink.dist, t0=ink.t0)
    fold = fold_mod.estimate_fold(lab, ink.mask, figure, 120.0, ink.t0)
    regions = region_stats(seg.fragments, lab)
    res = pairing_mod.pair_regions_fold(seg.fragments, fold.mirror(seg.fragments), lab, fold.mirror(lab), regions,
                                        lambda y: np.full(np.shape(y), 120.0), fold.band_half_width)
    sides = res["sides"]
    assert sum(v == "axis" for v in sides.values()) == 1        # the central column
    assert len(res["pairs"]) == 2 and all(p["iou"] > 0.8 for p in res["pairs"])
    for p in res["pairs"]:
        ca, cb = regions[p["a"]].centroid, regions[p["b"]].centroid
        assert ca[0] < 120 < cb[0] and abs(ca[1] - cb[1]) < 6    # left with right, same height
    # a colour that does not match rejects the geometric twin
    img2 = img.copy()
    img2[(seg.fragments == res["pairs"][0]["b"]) & ~ink.mask] = (60, 120, 200)
    lab2 = rgb_to_lab(img2)
    res2 = pairing_mod.pair_regions_fold(seg.fragments, fold.mirror(seg.fragments), lab2, fold.mirror(lab2),
                                         region_stats(seg.fragments, lab2), lambda y: np.full(np.shape(y), 120.0),
                                         fold.band_half_width)
    assert len(res2["pairs"]) == 1


# ----------------------------------------------------------------------------- line recovery
def test_faint_line_is_recovered_where_the_other_half_has_it():
    img = _drawing()
    img[78:83, 31:89] = (110, 85, 70)                            # left half: the dividing line came out faint
    lab, paper, ink, figure = _session_arrays(img)
    seg = overseg_mod.oversegment(figure, ink.mask, ink.dist, t0=ink.t0)
    fold = fold_mod.estimate_fold(lab, ink.mask, figure, 120.0, ink.t0)
    n_before = seg.n_leaves
    mask2, recs = complete_mod.complete_lines(seg.fragments, fold.mirror(seg.fragments), ink.mask & figure,
                                              fold.mirror(ink.mask & figure) > 0, lab, paper, ink.t0, 30)
    assert len(recs) == 1 and recs[0]["pieces"] == 2
    seg2 = overseg_mod.oversegment(figure, mask2, ink_mod.ink_distance(mask2), t0=ink.t0)
    assert seg2.n_leaves == n_before + 1


# ----------------------------------------------------------------------------- mirror-consistent merging
def test_symmetric_merge_keeps_every_level_mirror_consistent():
    img = _drawing()
    lab, paper, ink, figure = _session_arrays(img)
    seg = overseg_mod.oversegment(figure, ink.mask, ink.dist, t0=ink.t0)
    fold = fold_mod.estimate_fold(lab, ink.mask, figure, 120.0, ink.t0)
    regions = region_stats(seg.fragments, lab)
    leaf = pairing_mod.pair_regions_fold(seg.fragments, fold.mirror(seg.fragments), lab, fold.mirror(lab), regions,
                                         lambda y: np.full(np.shape(y), 120.0), fold.band_half_width)
    mr = merge_mod.build_merge_tree_symmetric(seg.fragments, ink.dist, ink.t0, 120.0, leaf)
    mr.tree.validate()
    assert len(mr.tree.merges) == seg.n_leaves - 1
    levels = merge_mod.choose_levels(mr.tree, mr.stats, {"levels": 6, "level_min_regions": 1})
    ends = {0} | {i + 1 for i, st in enumerate(mr.stats) if st["unit_end"]}
    assert all(lv["n_merges"] in ends for lv in levels)
    # at every level each off-axis region has a region of the same area on the other side
    for lv in levels:
        root = mr.tree.roots(lv["n_merges"])
        lm = root[seg.fragments]
        areas = {}
        for rid in np.unique(lm[lm > 0]):
            m = lm == rid
            cx = np.nonzero(m)[1].mean()
            areas.setdefault("L" if cx < 110 else "R" if cx > 130 else "C", []).append(int(m.sum()))
        left, right = sorted(areas.get("L", [])), sorted(areas.get("R", []))
        assert len(left) == len(right) and all(abs(a - b) <= 0.05 * max(a, b) for a, b in zip(left, right))
