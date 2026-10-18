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


def test_manual_groups_are_pinned_at_every_level(synthetic_dir):
    s = Session.load(synthetic_dir)
    gi = s.add_group([1, 5])
    gid = GROUP_ID_BASE + gi
    lab = s.label_map(0)
    assert set(np.unique(lab).tolist()) == {0, 2, 3, 4, gid}
    assert s.regions(0)[gid].leaves == (1, 5)
    # coarser: the composite stays exactly its leaves; region 6 (= 1 + 2) keeps only leaf 2
    assert set(np.unique(s.label_map(1)).tolist()) == {0, 6, 7, gid}
    assert s.regions(1)[gid].leaves == (1, 5) and s.regions(1)[6].leaves == (2,)
    # at the root level everything else is one region and the composite is still its own
    top = len(s.levels) - 1
    assert s.regions(top)[gid].leaves == (1, 5)
    assert sorted(s.regions(top)) == sorted([9, gid]) and s.regions(top)[9].leaves == (2, 3, 4)
    # without groups the level is untouched; cut_tile can take the level's own region under a group
    assert set(np.unique(s.label_map(1, apply_groups=False)).tolist()) == {0, 5, 6, 7}
    rgba, bbox = s.cut_tile(0, 1, apply_groups=False)
    assert bbox == (20, 30, 50, 60) and rgba[..., 3].min() == 255
    s2 = Session.load(synthetic_dir)  # persisted
    assert s2.groups == [[1, 5]]
    s2.remove_group(0)
    assert Session.load(synthetic_dir).groups == []


def test_mirror_leaves_folds_a_composite_as_a_whole(synthetic_dir):
    s = Session.load(synthetic_dir)
    assert s.mirror_leaves([1, 2]) == [3, 4]
    assert s.mirror_leaves([3]) == [1]
    # a composite that straddles the axis (the axis cell, or a twin pair) is its own mirror
    assert s.mirror_leaves([5]) == []
    assert s.mirror_leaves([2, 5]) == []
    assert s.mirror_leaves([1, 3]) == []
    assert s.mirror_leaves([]) == []


def test_cut_tile_roundtrip(synthetic_dir):
    s = Session.load(synthetic_dir)
    rgba, bbox = s.cut_tile(0, 2)
    assert bbox == (20, 100, 50, 70) and rgba.shape == (70, 50, 4)
    assert rgba[..., 3].min() == 255  # a rectangle fills its bbox
    data = encode_png(rgba)
    assert np.array_equal(decode_png(data), rgba)
    assert sha256(data) == sha256(encode_png(rgba))


def test_add_group_absorbs_overlapping_groups(synthetic_dir):
    """Merging a region that is already a manual group with another region yields one group, not a stack of
    groups that each need their own Unmerge."""
    s = Session.load(synthetic_dir)
    g0 = s.add_group([1, 2])
    assert s.groups == [[1, 2]]
    rid = GROUP_ID_BASE + g0
    leaves = s.region_leaves(0, rid) + s.region_leaves(0, 5)
    g1 = s.add_group(leaves)
    assert s.groups == [[1, 2, 5]] and g1 == 0
    assert set(np.unique(s.label_map(0)).tolist()) == {0, 3, 4, GROUP_ID_BASE}
    s.remove_group(0)
    assert s.groups == [] and set(np.unique(s.label_map(0)).tolist()) == {0, 1, 2, 3, 4, 5}
    # unrelated groups are kept, and keep their order
    s.add_group([3])
    s.add_group([1, 2])
    s.add_group([2, 5])
    assert s.groups == [[3], [1, 2, 5]]
