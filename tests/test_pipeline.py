"""Pipeline tests: fast unit tests on small synthetic arrays, plus `sample` tests on the real photo."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from biomorphic.pipeline import axis as axis_mod
from biomorphic.pipeline import ingest as ingest_mod
from biomorphic.pipeline import ink as ink_mod
from biomorphic.pipeline import merge as merge_mod
from biomorphic.pipeline import oversegment as overseg_mod
from biomorphic.pipeline import pairing as pairing_mod
from biomorphic.pipeline.debug import boundary_mask, write_debug_sheet
from biomorphic.pipeline.run import default_params, run_pipeline
from biomorphic.session import RegionInfo, Session, rgb_to_lab
from biomorphic.testing import synthetic_arrays

PAPER_RGB = (234, 211, 185)     # warm paper like the sample (Lab ~ 86, 4, 16)
INK_RGB = (28, 28, 30)          # black ink
NAVY_RGB = (28, 40, 95)         # navy pencil: as dark as ink but blue
TEAL_RGB = (120, 200, 170)


def _lab(rgb_img: np.ndarray) -> np.ndarray:
    return rgb_to_lab(np.ascontiguousarray(rgb_img, dtype=np.uint8))


def _paper_lab() -> np.ndarray:
    return _lab(np.full((4, 4, 3), PAPER_RGB, np.uint8))[0, 0]


# ----------------------------------------------------------------------------- ink
def test_ink_is_dark_and_colourless_relative_to_paper():
    img = np.full((80, 120, 3), PAPER_RGB, np.uint8)
    img[30:42, 10:110] = INK_RGB            # a 12 px thick ink line
    img[50:62, 10:110] = NAVY_RGB           # an equally dark navy band: not ink
    img[5:20, 10:110] = TEAL_RGB            # colour fill: not ink
    res = ink_mod.detect_ink(_lab(img), _paper_lab())
    assert res.mask[30:42, 20:100].all()
    assert not res.mask[50:62, 10:110].any()
    assert not res.mask[5:20, 10:110].any()
    assert res.mask.sum() == 12 * 100
    assert 5.0 <= res.t0 <= 7.0                 # half of a 12 px line
    # the darkness test is relative to the paper: a darker photo keeps the same ink
    dark_img = (img.astype(np.float32) * 0.6).astype(np.uint8)
    paper_dark = _lab(np.full((4, 4, 3), tuple(int(v * 0.6) for v in PAPER_RGB), np.uint8))[0, 0]
    res2 = ink_mod.detect_ink(_lab(dark_img), paper_dark)
    assert res2.mask[30:42, 20:100].all() and not res2.mask[50:62, 10:110].any()


def test_ink_cleaning_removes_specks_and_closes_gaps():
    m = np.zeros((40, 60), bool)
    m[10:16, 5:30] = True
    m[10:16, 31:55] = True                      # 1 px gap in a line
    m[30, 30] = True                            # a speck
    out = ink_mod.clean_mask(m, speck_area=24, close_px=1)
    assert out[10:16, 30].all()                 # gap closed
    assert not out[30, 30]                      # speck gone


# ----------------------------------------------------------------------------- axis
def test_axis_profile_matches_exact_reflection_convention():
    rng = np.random.default_rng(0)
    m = np.zeros((60, 90), bool)
    m[10:50, 20:70] = rng.random((40, 50)) > 0.4
    prof = axis_mod.iou_profile(m)
    for x in (40.0, 44.5, 45.0, 47.5):
        assert abs(prof[int(round(2 * x))] - axis_mod.mirror_iou(m, x)) < 1e-9


def test_axis_search_finds_symmetric_axis_and_angle():
    _rgb, figure, _ink, _frags, _tree = synthetic_arrays()      # symmetric about x = 120
    res = axis_mod.find_axis(figure, {"axis_angle_step": 0.0})
    assert res["x"] == 120.0 and res["angle_deg"] == 0.0
    assert res["score"] > 0.99 and res["mirror_iou"] > 0.99
    assert res["search"]["x_min"] < 120 < res["search"]["x_max"]
    # a blobby symmetric shape, rotated: the search must report the angle that undoes the rotation
    yy, xx = np.mgrid[0:240, 0:240]
    blob = ((xx - 120) ** 2 / 60.0 ** 2 + (yy - 120) ** 2 / 90.0 ** 2 < 1) | \
           ((np.abs(xx - 120) < 15) & (yy > 30) & (yy < 215)) | \
           (((xx - 70) ** 2 + (yy - 170) ** 2 < 20 ** 2) | ((xx - 169) ** 2 + (yy - 170) ** 2 < 20 ** 2))
    tilted = axis_mod.rotate_mask(blob, 2.0)
    res = axis_mod.find_axis(tilted, {"axis_angle_max": 3.0, "axis_angle_step": 0.5, "axis_coarse_scale": 2})
    assert res["angle_deg"] == -2.0
    restored = axis_mod.rotate_mask(tilted, res["angle_deg"])
    assert axis_mod.mirror_iou(restored, res["x"]) > 0.95
    assert abs(res["x"] - 120.0) <= 1.5


# ----------------------------------------------------------------------------- oversegment
def _grid_figure(h=120, w=160, ink_w=6):
    """A figure of 2x3 ink-bordered cells (ink lines ink_w px wide), paper around it."""
    figure = np.zeros((h, w), bool)
    figure[10:110, 10:150] = True
    ink = np.zeros((h, w), bool)
    for x in (10, 56, 102, 144):
        ink[10:110, x:x + ink_w] = True
    for y in (10, 57, 104):
        ink[y:y + ink_w, 10:150] = True
    return figure, ink


def test_watershed_covers_the_figure_with_one_leaf_per_cell():
    figure, ink = _grid_figure()
    dist = ink_mod.ink_distance(ink)
    res = overseg_mod.oversegment(figure, ink, dist, {"cell_min_area": 30})
    frags = res.fragments
    assert res.n_cells == 6 and res.n_leaves == 6
    assert np.array_equal(frags > 0, figure)                  # every figure pixel labelled, nothing else
    assert sorted(np.unique(frags[figure]).tolist()) == list(range(1, 7))
    # boundaries between leaves lie on the ink (the centre line of the lines)
    b = boundary_mask(frags)
    assert b.any() and (b & ink).sum() / b.sum() > 0.95
    # neighbours share a border without a gutter: labels differ across the centre line directly
    assert frags[50, 58] != frags[50, 60]


def test_extra_seeds_split_big_cells_and_merge_first():
    figure, ink = _grid_figure(h=260, w=260, ink_w=4)
    figure[:] = False
    figure[10:250, 10:250] = True
    ink[:] = False
    ink[10:250, 10:14] = ink[10:250, 246:250] = True
    ink[10:14, 10:250] = ink[246:250, 10:250] = True
    ink[128:132, 10:250] = True                                # two big cells
    dist = ink_mod.ink_distance(ink)
    plain = overseg_mod.oversegment(figure, ink, dist, {"cell_min_area": 30})
    seeded = overseg_mod.oversegment(figure, ink, dist, {"cell_min_area": 30, "extra_seeds": True,
                                                         "extra_seed_min_area": 5000, "extra_seed_spacing": 80})
    assert plain.n_leaves == 2 and seeded.n_leaves > 2
    assert np.array_equal(seeded.fragments > 0, figure)
    mr = merge_mod.build_merge_tree(seeded.fragments, dist, 2.0, 130.0)
    levels = merge_mod.choose_levels(mr.tree, mr.stats, {"levels": 6, "level_min_regions": 1})
    n_no_ink = sum(1 for s in mr.stats if s["ink_norm"] < 0.1)
    assert n_no_ink == seeded.n_leaves - 2                      # every seed border is ink-free ...
    assert all(s["ink_norm"] < 0.1 for s in mr.stats[:n_no_ink])  # ... and those merges come first
    assert levels[0]["n_merges"] == n_no_ink and levels[0]["n_regions"] == 2


# ----------------------------------------------------------------------------- merge
def test_merge_tree_is_valid_monotone_and_ink_dominated():
    figure, ink = _grid_figure()
    dist = ink_mod.ink_distance(ink)
    seg = overseg_mod.oversegment(figure, ink, dist)
    regions, edges = merge_mod.build_rag(seg.fragments, dist, axis_x=80.0)
    assert len(regions) == 6 and len(edges) == 7              # 2x3 grid: 3 + 4 shared borders
    for (a, b), (bl, isum) in edges.items():
        assert bl > 0 and isum / bl > 1.0                      # borders run along the ink centre line
    assert all(r.perimeter > 0 and r.area > 0 for r in regions.values())
    mr = merge_mod.build_merge_tree(seg.fragments, dist, 3.0, 80.0)
    tree = mr.tree
    tree.validate()
    assert tree.n_leaves == 6 and len(tree.merges) == 5
    costs = [m[2] for m in tree.merges]
    assert costs == sorted(costs)
    assert all(m[3] == 6 + i + 1 for i, m in enumerate(tree.merges))
    assert all(r == tree.roots(5)[1] for r in tree.roots(5)[1:7])
    levels = merge_mod.choose_levels(tree, mr.stats, {"levels": 4, "level_min_regions": 1})
    assert levels[0] == {"index": 0, "n_merges": 0, "n_regions": 6, "cost": 0.0}
    assert [lv["index"] for lv in levels] == list(range(len(levels)))
    assert all(a["n_merges"] < b["n_merges"] for a, b in zip(levels, levels[1:]))
    assert levels[-1]["n_regions"] == 1


def test_merge_cost_prefers_embedded_small_cells_and_penalises_cross_axis():
    p = merge_mod.DEFAULTS
    big = merge_mod.Region(area=10000, perimeter=400, x0=0, y0=0, x1=100, y1=100, sx=0, sy=0,
                           area_left=10000, area_right=0, side="L")
    small = merge_mod.Region(area=400, perimeter=80, x0=0, y0=0, x1=20, y1=20, sx=0, sy=0,
                             area_left=400, area_right=0, side="L")
    other = merge_mod.Region(area=10000, perimeter=400, x0=0, y0=0, x1=100, y1=100, sx=0, sy=0,
                             area_left=0, area_right=10000, side="R")
    t0, area_ref = 5.0, 5000.0
    c_embedded, i1 = merge_mod.edge_cost(big, small, 80, 80 * 5.0, t0, area_ref, p)        # fully enclosed
    c_touching, i2 = merge_mod.edge_cost(big, small, 20, 20 * 5.0, t0, area_ref, p)        # quarter of its perimeter
    c_thin, _ = merge_mod.edge_cost(big, small, 80, 80 * 1.0, t0, area_ref, p)             # thin ink
    c_same, _ = merge_mod.edge_cost(big, big, 100, 100 * 5.0, t0, area_ref, p)
    c_cross, i3 = merge_mod.edge_cost(big, other, 100, 100 * 5.0, t0, area_ref, p)
    assert i1["ink_norm"] == pytest.approx(1.0) and i1["embed"] == pytest.approx(1.0)
    assert c_embedded < c_touching
    assert c_thin < c_embedded
    assert c_embedded < c_same                 # smaller min-area is cheaper
    assert c_cross > c_same and i3["cross"] and not i2["cross"]


# ----------------------------------------------------------------------------- pairing
def _blob(h, w):
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - w / 2 + 0.5) ** 2 / (w / 2) ** 2 + (yy - h / 2 + 0.5) ** 2 / (h / 2) ** 2) <= 1.0


def _region(label_map: np.ndarray, lab: np.ndarray, rid: int) -> RegionInfo:
    ys, xs = np.nonzero(label_map == rid)
    return RegionInfo(id=rid, area=int(ys.size), bbox=(int(xs.min()), int(ys.min()),
                      int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)),
                      centroid=(float(xs.mean()), float(ys.mean())),
                      mean_lab=tuple(float(v) for v in lab[ys, xs].mean(axis=0)), leaves=(rid,))


def test_pairing_registration_finds_a_known_shift():
    h, w, axis_x = 160, 240, 120.0
    lm = np.zeros((h, w), np.int32)
    blob = _blob(40, 30)
    lm[50:90, 20:50][blob] = 1                                     # L region
    # its exact reflection would sit at columns floor(240 - x - 0.5): 219..190, i.e. [190, 220)
    dx, dy = 7, -5
    lm[50 + dy:90 + dy, 190 + dx:220 + dx][blob] = 2               # R twin, shifted by (dx, dy)
    lm[60:100, 105:135][_blob(40, 30)] = 3                         # an axis region
    rgb = np.full((h, w, 3), PAPER_RGB, np.uint8)
    rgb[lm > 0] = TEAL_RGB
    lab = rgb_to_lab(rgb)
    regions = {rid: _region(lm, lab, rid) for rid in (1, 2, 3)}
    res = pairing_mod.pair_regions(lm, axis_x, lab, regions)
    assert res["sides"] == {1: "L", 2: "R", 3: "axis"}
    assert res["twin"] == {1: 2, 2: 1}
    pr = res["pairs"][0]
    assert pr["iou"] > 0.99 and pr["dx"] == dx and pr["dy"] == dy
    assert pr["rigid_iou"] < 0.75                                  # registration beats the rigid reflection
    # a colour mismatch rejects the geometric match
    rgb2 = rgb.copy()
    rgb2[lm == 2] = (40, 40, 120)
    lab2 = rgb_to_lab(rgb2)
    regions2 = {rid: _region(lm, lab2, rid) for rid in (1, 2, 3)}
    assert pairing_mod.pair_regions(lm, axis_x, lab2, regions2)["pairs"] == []


def test_register_masks_large_frame_path_and_scale_option():
    a = np.zeros((700, 700), bool)
    a[200:400, 150:350] = True
    b = np.roll(np.roll(a, 9, axis=0), -13, axis=1)
    iou, dy, dx = pairing_mod.register_masks(a, b, max_shift=30, fft_max_side=256)
    assert abs(dy - 9) <= 1 and abs(dx + 13) <= 1 and iou > 0.95
    # +-10 % scale search on a twin drawn 10 % larger
    h, w = 160, 240
    lm = np.zeros((h, w), np.int32)
    lm[50:90, 20:50][_blob(40, 30)] = 1
    lm[48:92, 189:222][_blob(44, 33)] = 2
    rgb = np.full((h, w, 3), PAPER_RGB, np.uint8)
    rgb[lm > 0] = TEAL_RGB
    lab = rgb_to_lab(rgb)
    regions = {rid: _region(lm, lab, rid) for rid in (1, 2)}
    plain = pairing_mod.pair_regions(lm, 120.0, lab, regions)["pairs"][0]
    scaled = pairing_mod.pair_regions(lm, 120.0, lab, regions, {"pair_scales": [0.9, 1.0, 1.1]})["pairs"][0]
    assert scaled["scale"] == 1.1 and scaled["iou"] > plain["iou"]


# ----------------------------------------------------------------------------- ingest
def test_load_oriented_applies_exif_orientation(tmp_path: Path):
    rgb = np.full((60, 100, 3), PAPER_RGB, np.uint8)       # landscape pixels ...
    rgb[20:40, 10:30] = INK_RGB
    exif = Image.Exif()
    exif[274] = 6                                             # ... that display as portrait
    path = tmp_path / "sideways.jpg"
    Image.fromarray(rgb).save(path, quality=95, exif=exif.tobytes())
    src = ingest_mod.load_oriented(path)
    assert src.rgb.shape[:2] == (100, 60) and src.exif_orientation == 6
    assert len(src.sha256) == 64
    plain = tmp_path / "plain.png"
    Image.fromarray(rgb).save(plain)
    assert ingest_mod.load_oriented(plain).rgb.shape[:2] == (60, 100)


def test_figure_box_crop_and_working_copy():
    rgb = np.full((300, 400, 3), PAPER_RGB, np.uint8)
    rgb[100:200, 150:250] = TEAL_RGB                          # colour fill
    rgb[100:106, 150:250] = INK_RGB                           # ink outline on top
    rgb[50, :] = (200, 180, 160)                              # a faint pencil grid line: not figure
    box, paper = ingest_mod.find_figure_box(rgb, {"figure_box_width": 200})
    assert abs(box[0] - 150) <= 3 and abs(box[1] - 100) <= 3 and abs(box[2] - 100) <= 4 and abs(box[3] - 100) <= 4
    assert abs(paper[0] - _paper_lab()[0]) < 2
    crop, rec = ingest_mod.crop_with_margin(rgb, box, 0.1)
    assert rec["margin_px"] == 10 and crop.shape[1] == rec["w"] and rec["figure_box"]["w"] == box[2]
    small, scale = ingest_mod.make_working(crop, 60)
    assert small.shape[1] == 60 and scale == pytest.approx(60 / crop.shape[1])
    same, scale1 = ingest_mod.make_working(crop, 1800)         # never upscaled
    assert same.shape == crop.shape and scale1 == 1.0
    lab, mask, wbox = ingest_mod.analyse_working(crop, paper, {})
    assert mask.sum() == pytest.approx(100 * 100, rel=0.05) and not mask[0, 0]
    rotated = ingest_mod.rotate_rgb(crop, 2.0, tuple(PAPER_RGB))
    assert rotated.shape == crop.shape


def test_default_params_cover_every_module():
    p = default_params()
    for mod in (ingest_mod, ink_mod, axis_mod, overseg_mod, merge_mod, pairing_mod):
        for k, v in mod.DEFAULTS.items():
            assert p[k] == v


# ----------------------------------------------------------------------------- sample
@pytest.mark.sample
def test_sample_session_integrity_counts_and_pairing(sample_photo: Path, tmp_path: Path):
    t = time.perf_counter()
    out = run_pipeline(sample_photo, tmp_path / "session", debug=False)
    elapsed = time.perf_counter() - t
    assert elapsed < 60
    s = Session.load(out)
    m = s.meta
    assert m["source"]["width"] == 1932 and m["source"]["height"] == 2576
    assert m["working"]["width"] <= 1800 and m["working"]["scale"] <= 1.0
    assert len(s.levels) >= 20 and s.levels[0]["index"] == 0
    assert set(m["params"]) >= set(default_params())
    assert m["counts"]["fragments"] == s.tree.n_leaves
    # axis near the figure's centre, strongly symmetric silhouette
    fb = m["measured"]["figure_box_working"]
    assert abs(s.axis_x - (fb[0] + fb[2] / 2)) < 0.05 * fb[2]
    assert m["axis"]["mirror_iou"] > 0.9
    # partition integrity and ink fidelity at every level
    ink_d = cv2.dilate(s.ink_mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    for lv in s.levels:
        lm = s.label_map(lv["index"])
        assert np.array_equal(lm > 0, s.figure_mask)
        b = boundary_mask(lm)
        if b.any():
            assert (b & ink_d).sum() / b.sum() >= 0.9
    # granularity: some level with 50..75 regions, ~12 on-axis, < 30 per side, >= 80 % of off-axis paired
    target = [lv for lv in s.levels if 50 <= lv["n_regions"] <= 75]
    assert target
    best = None
    for lv in target:
        pr = s.pairing(lv["index"])
        sides = pr["sides"]
        n_axis = sum(v == "axis" for v in sides.values())
        n_l = sum(v == "L" for v in sides.values())
        n_r = sum(v == "R" for v in sides.values())
        rate = 2 * len(pr["pairs"]) / max(1, n_l + n_r)
        ok = 6 <= n_axis <= 20 and n_l < 30 and n_r < 30 and rate >= 0.8
        if ok and (best is None or rate > best[0]):
            best = (rate, lv["index"], n_axis, n_l, n_r)
        assert all(p["iou"] >= 0.35 for p in pr["pairs"])
    assert best is not None, "no level in the 50..75 band meets the pairing targets"


@pytest.mark.sample
def test_sample_debug_images_and_sheet(sample_photo: Path, tmp_path: Path):
    out = run_pipeline(sample_photo, tmp_path / "session", debug=True)
    names = {p.name for p in (out / "debug").glob("*.png")}
    assert {"ink.png", "axis.png", "fragments.png"} <= names
    assert any(n.startswith("boundaries_L") for n in names) and any(n.startswith("pairs_L") for n in names)
    sheet = write_debug_sheet(out)
    assert sheet.exists() and cv2.imread(str(sheet)) is not None
