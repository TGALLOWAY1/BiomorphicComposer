"""Grouping cost: projection of map pieces onto the drawing and the gesture-cost model, on the synthetic
session (cells 1..5, tree 1+2 -> 6, 3+4 -> 7, 6+7 -> 8, 8+5 -> 9; levels 5, 3, 2, 1 regions)."""
from __future__ import annotations

import numpy as np
import pytest

from biomorphic.session import Session
from biomorphic.verify import groundtruth as gt
from biomorphic.verify.checks import mask_iou
from biomorphic.verify.grouping import (best_single_level, grouping_cost, leaves_under_mask, level_cost,
                                        mirror_pairs, mixed_plan, project_piece, render_grouping_sheet,
                                        target_for_piece, write_grouping_report)


@pytest.fixture
def session(synthetic_dir):
    return Session.load(synthetic_dir)


def test_project_piece_identity_and_mirror(session):
    lm = session.label_map(0)
    regs = session.regions(0)
    m1 = lm == 1
    iou, tr = gt.shape_score(m1, m1, allow_mirror=False)
    back = project_piece(m1, tr, regs[1].centroid, regs[1].centroid, m1.shape)
    assert mask_iou(back, m1) > 0.97
    # cell 1 flipped lands on its twin, cell 3
    m3 = lm == 3
    iou, tr = gt.shape_score(m1, m3)
    assert tr.mirrored or iou > 0.9   # rectangles are symmetric, either way the projection must land on 3
    onto = project_piece(m1, tr, regs[1].centroid, regs[3].centroid, m1.shape)
    assert mask_iou(onto, m3) > 0.9


def test_project_piece_scales_between_frames(session):
    """A piece drawn at half size still projects onto the full-size region (the matcher is scale-free)."""
    lm = session.label_map(0)
    regs = session.regions(0)
    m2 = lm == 2
    small = np.zeros_like(m2)
    ys, xs = np.nonzero(m2)
    cy, cx = ys.mean(), xs.mean()
    small[((ys - cy) / 2 + cy).astype(int), ((xs - cx) / 2 + cx).astype(int)] = True
    iou, tr = gt.shape_score(small, m2, allow_mirror=False)
    assert tr.scale_a > tr.scale_b
    proj = project_piece(small, tr, (cx, cy), regs[2].centroid, m2.shape)
    assert mask_iou(proj, m2) > 0.9


def test_leaves_under_mask_majority_rule(session):
    lm = session.label_map(0)
    m = (lm == 1) | (lm == 2)
    assert leaves_under_mask(session, m) == [1, 2]
    half = m.copy()
    ys = np.nonzero(lm == 2)[0]
    y_cut = ys.min() + (ys.max() - ys.min()) // 3
    half[y_cut:, :] &= (lm != 2)[y_cut:, :]                           # keep only the top third of cell 2
    assert leaves_under_mask(session, half) == [1]
    assert leaves_under_mask(session, half, min_fraction=0.2) == [1, 2]


def test_level_cost_clean_and_straddling(session):
    # {1, 2} is node 6: one tap at level 1 (regions 6, 7, 5)
    c = level_cost(session, 1, [1, 2])
    assert c is not None and c.taps == 1 and c.merges == 0 and c.regions == (6,)
    # at level 0 it is two taps + merge
    c0 = level_cost(session, 0, [1, 2])
    assert c0 is not None and c0.taps == 2 and c0.merges == 1
    # {1, 5}: level 1 has region 6 = {1, 2} straddling the target
    assert level_cost(session, 1, [1, 5]) is None
    assert level_cost(session, 0, [1, 5]).taps == 2
    best = best_single_level(session, [1, 5])
    assert best.level == 0 and best.gestures == 3


def test_best_single_level_prefers_coarser_on_ties(session):
    # {1, 2, 3, 4} is node 8: one region at level 2; also two regions at level 1
    best = best_single_level(session, [1, 2, 3, 4])
    assert best.level == 2 and best.taps == 1
    # {1, 2, 5}: level 1 shows 6 and 5 -> 2 taps + merge; level 0 -> 3 taps + merge
    best = best_single_level(session, [1, 2, 5])
    assert best.level == 1 and best.taps == 2 and best.gestures == 3


def test_mixed_plan_descends(session):
    # {1, 2, 3, 4, 5}: everything = root at level 3 -> one tap, no merge, no level change
    plan = mixed_plan(session, [1, 2, 3, 4, 5])
    assert plan.taps == 1 and plan.merges == 0 and plan.level_changes == 0 and plan.gestures == 1
    # {1, 2, 3}: node 6 at level 1, then leaf 3 at level 0: 2 taps, 1 merge, 1 level change
    plan = mixed_plan(session, [1, 2, 3])
    assert [s.level for s in plan.steps] == [1, 0]
    assert plan.taps == 2 and plan.merges == 1 and plan.level_changes == 1 and plan.gestures == 4
    single = best_single_level(session, [1, 2, 3])
    assert single.level == 0 and single.gestures == 4   # same here; the mixed plan never costs more taps
    assert plan.taps <= single.taps


def _piece(session: Session, leaves: list[int], label: str, index: int):
    """A fake map piece: the leaf union itself, so the projection is an identity up to the matcher."""
    lm = session.label_map(0)
    mask = np.isin(lm, leaves)
    ys, xs = np.nonzero(mask)
    return gt.Piece(index=index, mask=mask, bbox=(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
                                                  int(ys.max() - ys.min() + 1)),
                    area=int(mask.sum()), centroid=(float(xs.mean()), float(ys.mean())),
                    mean_lab=(50.0, 0.0, 0.0), label=label)


def test_target_for_piece_recovers_leaves(session):
    p = _piece(session, [1, 2], "left", 1)
    iou, tr = gt.shape_score(p.mask, session.label_map(1) == 6)
    t = target_for_piece(session, p, 1, 6, tr, anchor_score=iou)
    assert t.leaves == (1, 2) and t.leaf_iou > 0.95 and t.settled
    # anchored on the wrong region (cell 5), the refinement still settles on a consistent leaf set
    iou5, tr5 = gt.shape_score(p.mask, session.label_map(0) == 5)
    t5 = target_for_piece(session, p, 0, 5, tr5, anchor_score=iou5)
    assert t5.leaves and t5.leaf_iou > 0.0


def test_mirror_pairs_from_level0_pairing(session):
    from biomorphic.verify.grouping import Target
    a = Target("L", 1, (1, 2), 1.0, 0, 6, 1.0, False, 1, True)
    b = Target("R", 2, (3, 4), 1.0, 0, 7, 1.0, False, 1, True)
    c = Target("axis", 3, (5,), 1.0, 0, 5, 1.0, False, 1, True)
    pairs = mirror_pairs(session, [a, b, c])
    assert [(x, y) for x, y, _ in pairs] == [("L", "R")]


def test_grouping_cost_end_to_end_synthetic(session, synthetic_dir, tmp_path):
    """Use the drawing itself as the 'map' (every cell is a piece) and check the report machinery."""
    map_rgb = session.working_rgb.copy()
    res = grouping_cost(session, map_rgb, None, max_seconds=30)
    s = res["summary"]
    assert s["n_pieces"] >= 1
    assert all(r["single"]["taps"] >= 1 for r in res["rows"])
    sheet = render_grouping_sheet(session, res, tmp_path / "sheet.png")
    md = write_grouping_report(res, tmp_path, sheet.name)
    assert sheet.exists() and md.exists()
    text = md.read_text()
    assert "## Per chunk" in text and "| chunk |" in text
    assert (tmp_path / "grouping_report.json").exists()
