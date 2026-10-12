"""Fast tests of the verify package on the synthetic session (no pipeline, no sample photo needed)."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from biomorphic.session import Session
from biomorphic.verify import checks as C
from biomorphic.verify.checks import CHECK_NAMES, CheckResult

# --------------------------------------------------------------------------- checks 1-5
def test_partition_integrity_passes_on_synthetic(synthetic_dir):
    s = Session.load(synthetic_dir)
    r = C.partition_integrity(s)
    assert isinstance(r, CheckResult) and r.name == "partition_integrity"
    assert r.passed is True, r.summary
    assert len(r.details["levels"]) == len(s.levels)
    assert all(x["cover_ok"] and x["recomposite_ok"] for x in r.details["levels"])
    assert r.details["worst_mismatch_px"] == 0


def test_partition_integrity_detects_a_broken_label_map(synthetic_dir):
    s = Session.load(synthetic_dir)
    lab = s.label_map(0)
    lab[0:5, 0:5] = 1  # paint outside the figure (the cache hands back the live array)
    r = C.partition_integrity(s)
    assert r.passed is False
    assert r.details["levels"][0]["cover_mismatch_px"] == 25
    assert 0 in r.details["failing_levels"]


def test_ink_fidelity_numbers_per_level(synthetic_dir):
    s = Session.load(synthetic_dir)
    r = C.ink_fidelity(s, level=0)
    assert r.name == "ink_fidelity" and r.passed is not None
    rows = r.details["levels"]
    assert [x["index"] for x in rows] == [lv["index"] for lv in s.levels]
    assert all({"n_boundary_px", "on_ink_px", "fraction"} <= set(x) for x in rows)
    # the synthetic cells are disjoint islands: no internal boundaries, vacuous pass
    assert rows[0]["n_boundary_px"] == 0 and r.details["vacuous"] is True and r.passed is True


def test_ink_fidelity_measures_a_real_boundary(synthetic_dir):
    s = Session.load(synthetic_dir)
    # glue cells 1 and 2 together with a strip of figure between them so they share a border
    lab0 = s.label_map(0)
    lab0[90:100, 20:70] = 1
    s.figure_mask[90:100, 20:70] = True
    r = C.ink_fidelity(s, level=0, dilate_px=0)
    row = r.details["levels"][0]
    assert row["n_boundary_px"] == 100  # 50 px wide border, both sides counted (rows 99 and 100)
    # cell 2's top ink border starts at y=100: the lower side is on ink, the upper side (y=99) is not
    assert row["on_ink_px"] == 50 and abs(row["fraction"] - 0.5) < 1e-9
    assert r.passed is False and r.details["measured"]["fraction_at_picked_level"] == 0.5
    # dilating the ink by 1 px reaches row 99 too
    r1 = C.ink_fidelity(s, level=0, dilate_px=1)
    assert r1.details["levels"][0]["fraction"] == 1.0 and r1.passed is True


def test_internal_boundaries_both_sides():
    lab = np.zeros((6, 6), np.int32)
    lab[:, :3] = 1
    lab[:, 3:] = 2
    b = C.internal_boundaries(lab)
    assert b[:, 2].all() and b[:, 3].all() and not b[:, :2].any() and not b[:, 4:].any()
    lab[:, 3:] = 0  # region against background is not internal
    assert not C.internal_boundaries(lab).any()
    assert C.all_boundaries(lab)[:, 2].all()


def test_axis_check(synthetic_dir):
    s = Session.load(synthetic_dir)
    r = C.axis_check(s)
    assert r.passed is True, r.summary
    assert r.details["axis_x"] == 120.0 and abs(r.details["offset_px"]) < 1e-9
    assert r.details["recomputed_mirror_iou"] > 0.99
    assert r.details["figure_box_working"] == {"x": 20, "y": 30, "w": 200, "h": 140}
    s.meta["axis"]["x"] = 40.0
    bad = C.axis_check(s)
    assert bad.passed is False and bad.details["measured"]["abs_offset_fraction"] > 0.15


def test_granularity_picks_a_level_and_reports_counts(synthetic_dir):
    s = Session.load(synthetic_dir)
    r = C.granularity(s)
    assert r.passed is False  # 5/3/2/1 regions is nowhere near 50-75
    picked = r.details["picked_level"]
    assert picked in range(len(s.levels))
    table = r.details["levels"]
    assert [x["n_regions"] for x in table] == [5, 3, 2, 1]
    assert table[0] == {**table[0], "n_axis": 1, "n_left": 2, "n_right": 2, "n_pairs": 2}
    assert all("distance" in x and "in_band" in x for x in table)
    assert C.pick_level(s)[0] == picked == 0


def test_band_distance_prefers_the_band():
    assert C.band_distance(60, 12, 24, 24) == 0.0
    assert C.band_distance(60, 12, 24, 24) < C.band_distance(60, 20, 24, 24) < C.band_distance(90, 12, 24, 24)
    assert C.band_distance(60, 12, 35, 24) > C.band_distance(60, 12, 29, 24)


def test_pairing_check_full_at_level_0(synthetic_dir):
    s = Session.load(synthetic_dir)
    r = C.pairing_check(s, 0)
    assert r.passed is True and r.details["fraction"] == 1.0
    assert r.details["n_off_axis"] == 4 and r.details["n_paired"] == 4 and r.details["unpaired"] == []
    s.meta["params"] = {"pair_min_iou": 2.0}  # nothing can pair now
    s2 = Session.load(synthetic_dir)
    s2.meta["params"] = {"pair_min_iou": 2.0}
    r2 = C.pairing_check(s2, 0)
    assert r2.passed is False and r2.details["fraction"] == 0.0
    assert {u["id"] for u in r2.details["unpaired"]} == {1, 2, 3, 4}
    assert all({"side", "area", "bbox"} <= set(u) for u in r2.details["unpaired"])


def test_checks_degrade_to_not_run(synthetic_dir, tmp_path):
    s = Session.load(synthetic_dir)
    r = C.ground_truth(s, 0, tmp_path / "missing_map.png", None)
    assert r.passed is None and r.summary.startswith("not run:") and "reason" in r.details
    r2 = C.pairing_check(s, 99)
    assert r2.passed is None and "IndexError" in r2.summary
    assert json.dumps(r.to_json())  # serialisable, traceback included as text


def test_ground_truth_check_on_synthetic(synthetic_dir):
    """The synthetic working copy doubles as the map: five coloured cells match their own regions."""
    s = Session.load(synthetic_dir)
    r = C.ground_truth(s, 0, synthetic_dir / "working.png", None)
    assert r.passed is True, r.summary
    d = r.details
    assert d["match_threshold"] == 0.8 and d["threshold"] == {"min_one_to_one_fraction": 0.5, "per_piece_iou": 0.8}
    assert d["n_pieces"] == 5 and d["one_to_one_fraction"] == 1.0 and d["one_to_one_matched"] == 5
    assert len(d["rows"]) == 5 and all({"margin", "runner_up", "assigned_tile_id", "transform"} <= set(x) for x in d["rows"])
    assert len(d["one_to_one"]) == 5 and all(x["matched"] for x in d["one_to_one"])
    assert [x["index"] for x in d["levels"]] == [0, 1, 2, 3] and d["levels_evaluated"] == [0, 1, 2, 3]
    assert d["best_level"]["index"] == 0 and d["never_matched"] == [] and d["why"] == []
    assert len(d["best_per_piece"]) == 5 and all(b["matched"] for b in d["best_per_piece"])
    assert d["labels_loaded"] is False and "no labels" in d["labels_note"]
    assert {"pieces", "candidates", "table", "map_rgb", "best_per_piece"} <= set(d["_objects"])
    assert "one-to-one 5/5" in r.summary and "best level 0" in r.summary
    json.dumps(r.to_json())
    # at level 2 only the axis cell survives as a single shape (the four side cells are one region), so one
    # of the five pieces matches one-to-one: the check fails and says why
    r2 = C.ground_truth(s, 2, synthetic_dir / "working.png", None)
    assert r2.passed is False and r2.details["one_to_one_fraction"] == pytest.approx(0.2)
    assert r2.details["why"] and "level 0" in r2.details["why"][0] and "why:" in r2.summary
    assert r2.details["measured"]["best_level"] == 0


def test_match_rows_adapts_tuples_dicts_and_objects():
    from dataclasses import dataclass

    @dataclass
    class Tr:
        def describe(self) -> str:
            return "mirror, +5deg"

    @dataclass
    class Row:
        label: str
        piece_index: int
        tile_id: int
        score: float
        matched: bool
        transform: Tr
        assigned_tile_id: int
        assigned_score: float
        assigned_matched: bool

    @dataclass
    class Table:
        rows: list

    rows = C.match_rows(Table([Row("A1", 0, np.int64(7), np.float32(0.8), True, Tr(), 9, 0.7, True)]))
    assert rows == [{"label": "A1", "tile_id": 7, "score": rows[0]["score"], "matched": True, "piece_index": 0,
                     "assigned_tile_id": 9, "assigned_score": 0.7, "assigned_matched": True,
                     "transform": "mirror, +5deg"}]
    assert abs(rows[0]["score"] - 0.8) < 1e-6
    assert C.match_rows([("B2", "tile-x", 0.4, False)]) == [{"label": "B2", "tile_id": "tile-x", "score": 0.4, "matched": False}]
    assert C.match_rows({"rows": [{"label": None, "tile_id": None, "score": None, "matched": None}]}) == \
        [{"label": None, "tile_id": None, "score": None, "matched": None}]
    json.dumps(rows)


def test_jsonable_strips_private_keys_and_numpy():
    d = {"a": np.int64(3), "b": np.float32(1.5), "_objects": {"big": np.zeros((100, 100))},
         "p": Path("x"), "t": (1, 2), "arr": np.arange(3), "nan": float("nan")}
    out = C.jsonable(d)
    assert out == {"a": 3, "b": 1.5, "p": "x", "t": [1, 2], "arr": [0, 1, 2], "nan": None}
    json.dumps(out)


# --------------------------------------------------------------------------- contact sheet
def test_contact_sheet_written(synthetic_dir, tmp_path):
    from biomorphic.verify.contact_sheet import write_contact_sheet
    s = Session.load(synthetic_dir)
    p = write_contact_sheet(s, 0, tmp_path / "sheet.png")
    img = cv2.imread(str(p))
    assert img is not None and img.shape[0] > 800 and img.shape[1] > 800
    # the magenta outline colour is present (BGR in the file)
    assert np.any(np.all(img == (170, 40, 255), axis=2))


# --------------------------------------------------------------------------- run_verify
def _tiny_map(path: Path) -> Path:
    img = np.full((120, 160, 3), 255, np.uint8)
    img[20:60, 20:60] = (60, 90, 200)
    img[20:70, 100:140] = (90, 200, 120)
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return path


def test_run_verify_synthetic_fallback_writes_reports(tmp_path, monkeypatch):
    from biomorphic.pipeline import run as pipeline_run
    from biomorphic.verify.run import run_verify

    def unavailable(*_a, **_k):
        raise NotImplementedError("pipeline stubbed for the test")

    monkeypatch.setattr(pipeline_run, "run_pipeline", unavailable)
    out = tmp_path / "verify_out"
    res = run_verify(tmp_path / "photo.jpeg", _tiny_map(tmp_path / "map.png"), out, skip_ui=True)
    assert set(res) >= {"all_passed", "summary", "results", "paths"}
    assert "not run" in res["summary"] and "pipeline" in res["summary"]
    assert (out / "report.json").exists() and (out / "report.md").exists()
    md = (out / "report.md").read_text()
    rj = json.loads((out / "report.json").read_text())
    names = [c["name"] for c in rj["checks"]]
    for name in CHECK_NAMES:
        assert name in names and name in res["results"]
        assert name in md
    assert "SYNTHETIC" in md and rj["context"]["synthetic_fallback"] is True
    assert rj["checks"][0]["name"] == "pipeline" and rj["checks"][0]["passed"] is None
    assert res["results"]["partition_integrity"]["passed"] is True
    assert res["results"]["ui_drive"]["passed"] is None
    assert "Where the drawing disagrees" in md and "Per-level counts" in md
    assert (out / "session" / "session.json").exists()
    assert res["paths"]["contact_sheet"] and Path(res["paths"]["contact_sheet"]).exists()
    # all_passed ignores not-run checks: granularity is the only genuine failure on the fixture
    failed = [c["name"] for c in rj["checks"] if c["passed"] is False]
    assert failed == ["granularity"] and res["all_passed"] is False


# --------------------------------------------------------------------------- UI drive
def test_drive_ui_synthetic(synthetic_dir, tmp_path):
    pytest.importorskip("playwright.sync_api")
    from biomorphic.verify.ui_drive import drive_ui
    try:
        from biomorphic.server.app import make_server  # noqa: F401
    except (ImportError, NotImplementedError) as exc:
        pytest.skip(f"server unavailable: {exc}")
    res = drive_ui(synthetic_dir, tmp_path / "library", tmp_path / "out", port=0)
    err = res.get("error")
    if err and err.startswith("not run"):
        pytest.skip(err)
    assert "01_initial" in res["steps"] and Path(res["steps"]["01_initial"]).exists(), res
    assert err is None, res
    assert res["selected"] and res["twins"]
    assert set(res["steps"]) >= {"01_initial", "02_slider", "03_tap", "04_save", "05_tray"}
    assert res["saved_tile_ids"] and res["provenance_ok"]
