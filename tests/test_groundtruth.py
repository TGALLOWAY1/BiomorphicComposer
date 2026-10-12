import json
import re

import cv2
import numpy as np
import pytest

from biomorphic.session import Session
from biomorphic.verify import groundtruth as gt

LABEL_RE = re.compile(r"^[A-E][0-7](-[LRTMB])?$")


# ----------------------------------------------------------------------------- synthetic helpers
def _blob(kind: str = "a", h: int = 300, w: int = 300) -> np.ndarray:
    """A few asymmetric test shapes."""
    m = np.zeros((h, w), bool)
    yy, xx = np.mgrid[:h, :w]
    if kind == "a":            # disc with a rectangle to the right and a nub on top
        m |= (xx - 120) ** 2 + (yy - 150) ** 2 < 60 ** 2
        m[100:200, 150:230] = True
        m[60:110, 110:130] = True
    elif kind == "disc":
        m |= (xx - 150) ** 2 + (yy - 150) ** 2 < 80 ** 2
    elif kind == "bar":            # 7:1, inside the ~9:1 that fits the normalised frame
        m[135:165, 40:260] = True
    elif kind == "L":
        m[60:240, 60:110] = True
        m[190:240, 60:240] = True
    elif kind == "crescent":
        m |= (xx - 150) ** 2 + (yy - 150) ** 2 < 90 ** 2
        m &= (xx - 190) ** 2 + (yy - 150) ** 2 > 70 ** 2
    return m


def _affine(mask: np.ndarray, scale: float = 1.0, angle: float = 0.0, flip: bool = False,
            dx: float = 0, dy: float = 0, size: tuple[int, int] = (500, 500)) -> np.ndarray:
    src = mask.astype(np.float32)
    if flip:
        src = src[:, ::-1]
    h, w = src.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    m[0, 2] += dx + (size[1] - w) / 2
    m[1, 2] += dy + (size[0] - h) / 2
    return cv2.warpAffine(src, m, (size[1], size[0]), flags=cv2.INTER_LINEAR) >= 0.5


def _piece(index: int, mask: np.ndarray, lab=(50.0, -5.0, 0.0), label=None) -> gt.Piece:
    ys, xs = np.nonzero(mask)
    return gt.Piece(index=index, mask=mask, bbox=(int(xs.min()), int(ys.min()), int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1),
                    area=int(mask.sum()), centroid=(float(xs.mean()), float(ys.mean())), mean_lab=lab, label=label)


# ----------------------------------------------------------------------------- shape score
def test_shape_score_mirror_scale_translate():
    a = _blob("a")
    b = _affine(a, scale=1.4, flip=True, dx=37, dy=-22)
    score, tr = gt.shape_score(a, b)
    assert score > 0.9
    assert tr.mirrored is True
    assert abs(tr.scale_a / tr.scale_b - 1.4) < 0.05        # the normalisation undid the 1.4x scale


def test_shape_score_small_rotation():
    a = _blob("a")
    b = _affine(a, angle=7.0)
    score, tr = gt.shape_score(a, b)
    assert score > 0.85
    assert tr.mirrored is False and 5.0 <= tr.angle_deg <= 10.0
    coarse, _ = gt.shape_score(a, b, rotations=(-10.0, -5.0, 0.0, 5.0, 10.0))
    assert coarse > 0.85


def test_shape_score_different_shapes():
    assert gt.shape_score(_blob("disc"), _blob("bar"))[0] < 0.5
    assert gt.shape_score(_blob("L"), _blob("bar"))[0] < 0.5
    assert gt.shape_score(_blob("a"), _blob("a"))[0] > 0.99


def test_shape_score_mirror_can_be_disabled():
    a = _blob("L")
    b = _affine(a, flip=True)
    with_mirror, tr = gt.shape_score(a, b, allow_mirror=True)
    without, _ = gt.shape_score(a, b, allow_mirror=False)
    assert with_mirror > 0.95 and tr.mirrored
    assert without < with_mirror - 0.15


def test_color_score():
    assert gt.color_score((50, 0, 0), (50, 0, 0)) == pytest.approx(1.0)
    assert 0.3 < gt.color_score((50, 0, 0), (50, 25, 0)) < 0.4
    assert gt.color_score((50, 0, 0), (50, 60, 60)) < 0.05


# ----------------------------------------------------------------------------- labels
def test_assign_labels_nearest_centroid_and_leftovers():
    pieces = [_piece(1, _affine(_blob("disc"), 0.5, dx=-150, dy=-150)),
              _piece(2, _affine(_blob("L"), 0.5, dx=150, dy=-150)),
              _piece(3, _affine(_blob("bar"), 0.5, dx=0, dy=150))]
    labels = {"map": "x.png", "pieces": [
        {"label": "C1", "cx": pieces[0].centroid[0] + 4, "cy": pieces[0].centroid[1] - 3, "area": pieces[0].area,
         "confidence": "high"},
        {"label": "C7", "cx": pieces[1].centroid[0] - 5, "cy": pieces[1].centroid[1] + 2, "area": pieces[1].area,
         "confidence": "low"},
        {"label": "E4", "cx": 5.0, "cy": 5.0, "area": 400, "confidence": "high"},     # nothing near the corner
    ]}
    res = gt.assign_labels(pieces, labels)
    assert res.assigned == {1: "C1", 2: "C7"}
    assert pieces[0].label == "C1" and pieces[0].confidence == "high"
    assert pieces[1].label == "C7" and pieces[1].confidence == "low"
    assert pieces[2].label is None and res.unlabelled == [3]
    assert [e["label"] for e in res.unmatched] == ["E4"]
    assert res.labelled_fraction == pytest.approx(2 / 3)
    # a tighter tolerance drops the offset entries
    res2 = gt.assign_labels(pieces, labels, max_dist=0.0, min_dist_px=1.0)
    assert res2.assigned == {} and len(res2.unmatched) == 3


def test_load_labels_validates(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text(json.dumps({"map": "m.png", "pieces": [{"label": "B4-L", "cx": 1, "cy": 2, "area": 3}]}))
    assert gt.load_labels(good)["pieces"][0]["label"] == "B4-L"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"map": "m.png", "pieces": [{"label": "Z9", "cx": 1, "cy": 2, "area": 3}]}))
    with pytest.raises(ValueError):
        gt.load_labels(bad)


# ----------------------------------------------------------------------------- matching table
def test_match_pieces_finds_transformed_copies(tmp_path):
    kinds = ["a", "L", "bar", "crescent"]
    pieces = [_piece(i + 1, _blob(k), lab=(50.0, -5.0 * i, 3.0 * i), label=f"C{i + 1}") for i, k in enumerate(kinds)]
    cands = [(f"r{i}", _affine(_blob(k), scale=0.8 + 0.2 * i, angle=4.0 - 3.0 * i, flip=bool(i % 2), dx=11, dy=-7),
              (50.0, -5.0 * i, 3.0 * i)) for i, k in enumerate(kinds)]
    cands.append(("r_disc", _blob("disc"), (50.0, 0.0, 0.0)))                        # distractors
    cands.append(("r_tiny", np.pad(np.ones((2, 3), bool), 10), (50.0, 0.0, 0.0)))   # below min area -> skipped
    table = gt.match_pieces(pieces, cands, threshold=0.6)
    assert table.n_pieces == 4 and table.n_candidates == 5 and table.skipped_candidates == ["r_tiny"]
    for i, row in enumerate(table.rows):
        assert row.label == f"C{i + 1}"
        assert row.tile_id == f"r{i}" and row.assigned_tile_id == f"r{i}"
        assert row.score > 0.85 and row.matched and row.assigned_matched
        assert row.transform.mirrored is bool(i % 2)
    assert table.matched_fraction == 1.0 and table.assigned_matched_fraction == 1.0 and table.misses == []
    assert table.shape_scores.shape == (4, 5)
    # strict threshold: everything is a miss
    strict = gt.match_pieces(pieces, cands, threshold=0.999)
    assert strict.matched_fraction == 0.0 and strict.misses == ["C1", "C2", "C3", "C4"]
    md = table.to_markdown()
    assert "| C1 | r0 |" in md and "4/4 pieces matched" in md
    js = json.loads(json.dumps(table.to_json()))
    assert js["matched"] == 4 and js["rows"][0]["tile_id"] == "r0"
    out = gt.render_match_sheet(pieces, cands, table, tmp_path / "sheet.png")
    img = cv2.imread(str(out))
    assert img is not None and img.shape[0] > 100 and img.shape[1] > 300


def test_candidates_from_session(synthetic_dir):
    s = Session.load(synthetic_dir)
    cands = gt.candidates_from_session(s, 0)
    assert sorted(c[0] for c in cands) == [1, 2, 3, 4, 5]
    regs = s.regions(0)
    for rid, mask, lab in cands:
        assert mask.shape == (regs[rid].bbox[3], regs[rid].bbox[2])
        assert int(mask.sum()) == regs[rid].area
        assert tuple(lab) == tuple(regs[rid].mean_lab)
    # the synthetic session's own cells as "map pieces" match their regions one-to-one
    lab_map = s.label_map(0)
    pieces = [_piece(rid, lab_map == rid, lab=regs[rid].mean_lab, label=f"C{rid}") for rid in (1, 2, 5)]
    table = gt.match_pieces(pieces, cands, threshold=0.9)
    assert all(r.assigned_score > 0.99 for r in table.rows)


def test_match_map_to_session_synthetic(synthetic_dir):
    """The synthetic working copy doubles as a 'map': its five coloured cells segment and match their regions."""
    s = Session.load(synthetic_dir)
    pieces, table = gt.match_map_to_session(s.working_rgb, s, 0, threshold=0.9)
    assert len(pieces) == 5
    regs = s.regions(0)
    assert table.matched_fraction == 1.0 and table.assigned_matched_fraction == 1.0
    assert sorted(r.assigned_tile_id for r in table.rows) == [1, 2, 3, 4, 5]
    # cells 1, 3 and 5 are all 50x60 and 2, 4 are 50x70 in the synthetic session, so a match is judged by
    # shape class (bbox size) and the runner-up legitimately ties; the grey on-axis cell 5 is told apart from
    # the blue cells 1/3 only by the colour tie-break
    for row in table.rows:
        p = pieces[row.piece_index - 1]
        assert row.score > 0.99 and row.assigned_score > 0.99 and row.runner_up > 0.99
        for tid in (row.tile_id, row.assigned_tile_id):
            assert regs[tid].bbox[2:] == p.bbox[2:]
        assert row.transform.scale_a > 0 and row.transform.scale_b > 0
        if abs(regs[5].centroid[0] - p.centroid[0]) < 3:
            assert row.tile_id == 5 and row.assigned_tile_id == 5 and row.colour > 0.99


# ----------------------------------------------------------------------------- the real map
@pytest.mark.sample
def test_sample_map_segments_and_labels(sample_map, tmp_path):
    rgb = cv2.cvtColor(cv2.imread(str(sample_map), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    pieces = gt.segment_map(rgb)
    assert 20 <= len(pieces) <= 40
    assert [p.index for p in pieces] == list(range(1, len(pieces) + 1))
    assert all(p.mask.shape == rgb.shape[:2] and p.mask.sum() == p.area for p in pieces)
    labels = gt.load_labels(sample_map.with_suffix(".labels.json"))
    res = gt.assign_labels(pieces, labels)
    assert res.labelled_fraction >= 0.9
    labelled = [p.label for p in pieces if p.label]
    assert all(LABEL_RE.match(lab) for lab in labelled)
    assert len(set(labelled)) == len(labelled)
    out = gt.render_pieces_overlay(rgb, pieces, tmp_path / "pieces.png")
    assert out.exists()


@pytest.mark.sample
def test_sample_map_mirror_twins_match(sample_map):
    """Off-axis pieces find their mirror twin among the other map pieces (mirror allowed)."""
    rgb = cv2.cvtColor(cv2.imread(str(sample_map), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    pieces = gt.segment_map(rgb)
    gt.assign_labels(pieces, gt.load_labels(sample_map.with_suffix(".labels.json")))
    cands = [(p.label, p.mask, p.mean_lab) for p in pieces if p.label]
    table = gt.match_pieces([p for p in pieces if p.label], cands, colour_weight=0.0)
    ids = table.candidate_ids
    hits = total = 0
    for i, row in enumerate(table.rows):
        m = re.match(r"^([A-E])([1-7])(-([LR]))?$", row.label)
        if not m:
            continue
        letter, num, _, side = m.groups()
        if side:
            twin = f"{letter}{num}-{'R' if side == 'L' else 'L'}"
        elif letter in "CDE" and num != "4":
            twin = f"{letter}{8 - int(num)}"
        elif letter in "AB":
            twin = {"A2": "A4", "A4": "A2", "B1": "B2", "B2": "B1"}.get(row.label)
        else:
            twin = None
        if twin is None or twin not in ids:
            continue
        scores = table.shape_scores[i].copy()
        scores[ids.index(row.label)] = -1.0          # not itself
        total += 1
        hits += ids[int(scores.argmax())] == twin
    assert total >= 16
    assert hits / total >= 0.8
