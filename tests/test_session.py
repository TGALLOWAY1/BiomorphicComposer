import numpy as np
import pytest

from biomorphic.session import GROUP_ID_BASE, MergeTree, Session
from biomorphic.tiles import decode_png, encode_png, sha256


def test_tree_roots_and_validation():
    t = MergeTree(5, [[1, 2, 0.5, 6], [3, 4, 0.5, 7], [6, 7, 1.5, 8], [8, 5, 2.0, 9]])
    r0 = t.roots(0)
    assert r0.tolist() == list(range(10))
    r2 = t.roots(2)
    assert r2[1] == r2[2] == 6 and r2[3] == r2[4] == 7 and r2[5] == 5
    r4 = t.roots(4)
    assert all(r4[i] == 9 for i in range(1, 6))
    assert t.leaves_under(8) == [1, 2, 3, 4]
    assert t.n_merges_for_regions(3) == 2
    with pytest.raises(ValueError):
        MergeTree.from_json({"n_leaves": 2, "merges": [[1, 2, 0.5, 5]]})


def test_levels_label_maps_and_regions(synthetic_dir):
    s = Session.load(synthetic_dir)
    assert len(s.levels) == 4
    lab0 = s.label_map(0)
    assert set(np.unique(lab0).tolist()) == {0, 1, 2, 3, 4, 5}
    lab1 = s.label_map(1)
    assert set(np.unique(lab1).tolist()) == {0, 5, 6, 7}
    regs = s.regions(0)
    assert regs[1].bbox == (20, 30, 50, 60)
    assert regs[1].area == 50 * 60
    assert regs[1].leaves == (1,)
    assert s.regions(1)[6].leaves == (1, 2)
    # every figure pixel is labelled at every level, nothing outside the figure is
    for i in range(4):
        lab = s.label_map(i)
        assert np.array_equal(lab > 0, s.figure_mask)


def test_baseline_pairing(synthetic_dir):
    s = Session.load(synthetic_dir)
    p = s.pairing(0)
    assert p["sides"] == {1: "L", 2: "L", 3: "R", 4: "R", 5: "axis"}
    assert p["twin"] == {1: 3, 3: 1, 2: 4, 4: 2}
    assert all(pr["iou"] > 0.99 for pr in p["pairs"])
    p1 = s.pairing(1)
    assert p1["twin"] == {6: 7, 7: 6}


def test_manual_groups_union_regions(synthetic_dir):
    s = Session.load(synthetic_dir)
    gi = s.add_group([1, 5])
    gid = GROUP_ID_BASE + gi
    lab = s.label_map(0)
    assert set(np.unique(lab).tolist()) == {0, 2, 3, 4, gid}
    assert s.regions(0)[gid].leaves == (1, 5)
    # at a coarser level the group swallows the whole region containing leaf 1
    assert set(np.unique(s.label_map(1)).tolist()) == {0, 7, gid}
    s2 = Session.load(synthetic_dir)  # persisted
    assert s2.groups == [[1, 5]]
    s2.remove_group(0)
    assert Session.load(synthetic_dir).groups == []


def test_cut_tile_roundtrip(synthetic_dir):
    s = Session.load(synthetic_dir)
    rgba, bbox = s.cut_tile(0, 2)
    assert bbox == (20, 100, 50, 70) and rgba.shape == (70, 50, 4)
    assert rgba[..., 3].min() == 255  # a rectangle fills its bbox
    data = encode_png(rgba)
    assert np.array_equal(decode_png(data), rgba)
    assert sha256(data) == sha256(encode_png(rgba))
