"""Run the whole verification on a sample and write the report (docs/ARCHITECTURE.md, section Verify).

    run_verify(photo, map_png, out_dir, labels_path=None, working_width=None, skip_ui=False) -> dict

Writes <out_dir>/session (the pipeline output), <out_dir>/library (a fresh library), contact_sheet.png,
gt_matches.png (when the ground-truth module can render it), ui/NN_<step>.png, report.json and report.md.
Every stage is guarded: a module that is not implemented yet turns into a "not run" entry in the report
rather than a crash, and if the pipeline itself raises NotImplementedError the synthetic session stands in
(loudly flagged in the report) so that everything downstream can still be exercised.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

from biomorphic.verify import checks as C
from biomorphic.verify.checks import CheckResult, jsonable
from biomorphic.verify.report import overall, write_report

SYNTHETIC_NOTE = ("PIPELINE UNAVAILABLE: biomorphic.pipeline.run.run_pipeline raised NotImplementedError, so the "
                  "checks below ran on the SYNTHETIC session (biomorphic.testing), not on the photo")


class _Timer:
    def __init__(self) -> None:
        self.timings: dict[str, float] = {}

    def __call__(self, name: str) -> "_Stage":
        return _Stage(self, name)


class _Stage:
    def __init__(self, timer: _Timer, name: str) -> None:
        self.timer, self.name = timer, name

    def __enter__(self) -> "_Stage":
        self.t0 = time.time()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.timer.timings[self.name] = round(time.time() - self.t0, 3)


def _run_pipeline_or_fallback(photo: Path, session_dir: Path, working_width: int | None,
                              notes: list[str]) -> CheckResult:
    from biomorphic.pipeline import run as pipeline_run
    params: dict[str, Any] = {}
    if working_width:
        params["working_width"] = int(working_width)
    try:
        path = pipeline_run.run_pipeline(photo, session_dir, params=params or None)
        return CheckResult("pipeline", True, f"pipeline wrote {path}", {"session_dir": str(path), "photo": str(photo),
                                                                        "params": params, "synthetic": False})
    except NotImplementedError as exc:
        notes.append(SYNTHETIC_NOTE)
        from biomorphic.testing import synthetic_session
        path = synthetic_session(session_dir)
        return CheckResult("pipeline", None, f"not run: {exc}; synthetic session written to {path}",
                           {"session_dir": str(path), "photo": str(photo), "synthetic": True, "reason": str(exc)})
    except Exception as exc:  # noqa: BLE001
        notes.append(f"PIPELINE CRASHED ({type(exc).__name__}: {exc}); the checks below ran on the SYNTHETIC session")
        from biomorphic.testing import synthetic_session
        path = synthetic_session(session_dir)
        return CheckResult("pipeline", False, f"pipeline crashed: {type(exc).__name__}: {exc}; synthetic fallback used",
                           {"session_dir": str(path), "photo": str(photo), "synthetic": True,
                            "traceback": traceback.format_exc()})


def _open_library(library_dir: Path) -> tuple[Any, str | None]:
    try:
        from biomorphic.library.db import Library
        return Library.open(library_dir), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _rec_id(rec: Any) -> str | None:
    if rec is None:
        return None
    if isinstance(rec, str):
        return rec
    if isinstance(rec, dict):
        return rec.get("id")
    return getattr(rec, "id", None)


def _save_demo_tiles(library: Any, session: Any, level: int) -> dict:
    """Save a mirror pair (highest IoU) plus the largest on-axis region at `level`; returns ids and notes."""
    out: dict[str, Any] = {"tile_ids": [], "pair": None, "axis_region": None, "errors": []}
    pairing = session.pairing(level)
    regions = session.regions(level)
    pairs = sorted(pairing.get("pairs", []), key=lambda p: -float(p.get("iou", 0)))
    sides = pairing.get("sides", {})
    if pairs:
        a, b = int(pairs[0]["a"]), int(pairs[0]["b"])
        out["pair"] = {"a": a, "b": b, "iou": pairs[0].get("iou")}
        try:
            save_pair = getattr(library, "save_pair", None)
            ids: list[str] = []
            if callable(save_pair):
                res = save_pair(session, level, a, b)
                recs = list(res) if isinstance(res, (list, tuple)) else [res]
                ids = [i for i in (_rec_id(r) for r in recs) if i]
            if not ids:
                ra = library.save_tile(session, level, a)
                rb = library.save_tile(session, level, b, pair_with=_rec_id(ra))
                ids = [i for i in (_rec_id(ra), _rec_id(rb)) if i]
            out["tile_ids"] += ids
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"saving pair ({a}, {b}): {type(exc).__name__}: {exc}")
    axis_regions = [rid for rid in regions if sides.get(rid) == "axis"]
    if axis_regions:
        rid = max(axis_regions, key=lambda r: regions[r].area)
        out["axis_region"] = int(rid)
        try:
            rec = library.save_tile(session, level, int(rid))
            tid = _rec_id(rec)
            if tid:
                out["tile_ids"].append(tid)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"saving on-axis region {rid}: {type(exc).__name__}: {exc}")
    elif not pairs:
        # nothing paired and nothing on the axis: save the largest region so checks 7-8 still have a tile
        rid = max(regions, key=lambda r: regions[r].area)
        try:
            rec = library.save_tile(session, level, int(rid))
            tid = _rec_id(rec)
            if tid:
                out["tile_ids"].append(tid)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"saving region {rid}: {type(exc).__name__}: {exc}")
    return out


def _merge_level_rows(gran: CheckResult, ink: CheckResult, integ: CheckResult, picked: int | None) -> list[dict]:
    rows = [dict(r) for r in (gran.details.get("levels") or [])]
    ink_by = {r["index"]: r for r in (ink.details.get("levels") or [])}
    integ_by = {r["index"]: r for r in (integ.details.get("levels") or [])}
    for r in rows:
        i = r["index"]
        r["ink_fraction"] = ink_by.get(i, {}).get("fraction")
        ib = integ_by.get(i)
        r["integrity_ok"] = None if ib is None else bool(ib["cover_ok"] and ib["recomposite_ok"])
        r["picked"] = (i == picked)
    return rows


def run_verify(photo: Path, map_png: Path, out_dir: Path, labels_path: Path | None = None,
               working_width: int | None = None, skip_ui: bool = False) -> dict:
    photo, map_png, out_dir = Path(photo), Path(map_png), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if labels_path is None:
        labels_path = map_png.with_suffix(".labels.json")
    labels_path = Path(labels_path)
    timer = _Timer()
    notes: list[str] = []
    results: list[CheckResult] = []
    paths: dict[str, Any] = {"out_dir": str(out_dir)}
    session_dir = out_dir / "session"
    library_dir = out_dir / "library"

    # ---- pipeline -----------------------------------------------------------------------------
    with timer("pipeline"):
        pipe = _run_pipeline_or_fallback(photo, session_dir, working_width, notes)
    results.append(pipe)
    paths["session_dir"] = str(session_dir)
    from biomorphic.session import Session
    session = Session.load(session_dir)

    # ---- library ------------------------------------------------------------------------------
    with timer("library_open"):
        library, lib_err = _open_library(library_dir)
    paths["library_dir"] = str(library_dir)
    if lib_err:
        notes.append(f"library unavailable: {lib_err}")

    # ---- checks 1-5 -----------------------------------------------------------------------------
    with timer("check_1_partition_integrity"):
        integ = C.partition_integrity(session)
    with timer("check_4_granularity"):
        gran = C.granularity(session)
    picked = gran.details.get("picked_level")
    if picked is None:
        try:
            picked, _ = C.pick_level(session)
            notes.append(f"granularity check did not run; picked level {picked} computed directly")
        except Exception as exc:  # noqa: BLE001
            picked = 0
            notes.append(f"could not pick a level ({type(exc).__name__}: {exc}); using level 0")
    picked = int(picked)
    with timer("check_2_ink_fidelity"):
        ink = C.ink_fidelity(session, level=picked)
    with timer("check_3_axis"):
        axis = C.axis_check(session)
    with timer("check_5_pairing"):
        pairing = C.pairing_check(session, picked)
    results += [integ, ink, axis, gran, pairing]

    # ---- check 6 --------------------------------------------------------------------------------
    with timer("check_6_ground_truth"):
        if map_png.exists():
            gt = C.ground_truth(session, picked, map_png, labels_path if labels_path.exists() else None)
            if not labels_path.exists():
                notes.append(f"labels file not found: {labels_path} (ground truth ran with unlabeled pieces)")
        else:
            gt = C.not_run("ground_truth", f"map not found: {map_png}")
    results.append(gt)

    # ---- tiles for checks 7-8 -------------------------------------------------------------------
    saved: dict[str, Any] = {"tile_ids": [], "errors": []}
    with timer("save_tiles"):
        if library is not None:
            try:
                saved = _save_demo_tiles(library, session, picked)
            except Exception as exc:  # noqa: BLE001
                saved = {"tile_ids": [], "errors": [f"{type(exc).__name__}: {exc}"]}
            for e in saved.get("errors", []):
                notes.append(f"tile save: {e}")
    paths["saved_tile_ids"] = list(saved.get("tile_ids", []))

    # ---- checks 7-8 -----------------------------------------------------------------------------
    with timer("check_7_non_destructive"):
        if library is None:
            nd = C.not_run("non_destructive", f"library unavailable: {lib_err}")
        else:
            nd = C.non_destructive(library, saved.get("tile_ids", []), library_dir=library_dir)
    with timer("check_8_provenance"):
        if library is None:
            prov = C.not_run("provenance_immutable", f"library unavailable: {lib_err}")
        else:
            prov = C.provenance_immutable(library, library_dir=library_dir)
    results += [nd, prov]

    # ---- contact sheet --------------------------------------------------------------------------
    with timer("contact_sheet"):
        try:
            from biomorphic.verify.contact_sheet import write_contact_sheet
            sheet = write_contact_sheet(session, picked, out_dir / "contact_sheet.png",
                                        gt=gt if gt.passed is not None else None)
            paths["contact_sheet"] = str(sheet)
        except Exception as exc:  # noqa: BLE001
            paths["contact_sheet"] = None
            notes.append(f"contact sheet failed: {type(exc).__name__}: {exc}")
    with timer("gt_match_sheet"):
        objs = gt.details.get("_objects") if gt.passed is not None else None
        if objs:
            try:
                from biomorphic.verify import groundtruth as gt_mod
                p = out_dir / "gt_matches.png"
                gt_mod.render_match_sheet(objs["pieces"], objs["candidates"], objs["table"], p)
                paths["gt_matches"] = str(p) if p.exists() else None
            except Exception as exc:  # noqa: BLE001
                paths["gt_matches"] = None
                notes.append(f"render_match_sheet failed: {type(exc).__name__}: {exc}")
        gt.details.pop("_objects", None)

    # ---- UI drive -------------------------------------------------------------------------------
    with timer("ui_drive"):
        if skip_ui:
            ui = CheckResult("ui_drive", None, "not run: --skip-ui", {"skipped": True})
        else:
            try:
                from biomorphic.verify.ui_drive import drive_ui
                d = drive_ui(session_dir, library_dir, out_dir, level=picked)
                err = d.get("error")
                if err is None:
                    ui = CheckResult("ui_drive", True,
                                     f"{len(d.get('steps', {}))} screenshots; selected={d.get('selected')!r} "
                                     f"twins={d.get('twins')!r}; saved {d.get('saved_tile_ids')} with provenance", d)
                elif str(err).startswith("not run"):
                    ui = CheckResult("ui_drive", None, err, d)
                else:
                    ui = CheckResult("ui_drive", False, f"failed at {err}", d)
            except Exception as exc:  # noqa: BLE001
                ui = C.not_run("ui_drive", exc)
        paths["screenshots"] = dict(ui.details.get("steps", {}))
    results.append(ui)

    # ---- report ---------------------------------------------------------------------------------
    level_rows = _merge_level_rows(gran, ink, integ, picked)
    context = {
        "photo": str(photo), "map": str(map_png), "labels": str(labels_path), "session_dir": str(session_dir),
        "session_id": session.session_id, "pipeline_version": session.meta.get("pipeline_version"),
        "working": session.meta.get("working"), "axis": session.meta.get("axis"),
        "picked_level": picked, "synthetic_fallback": bool(pipe.details.get("synthetic")),
        "n_levels": len(session.levels), "skip_ui": skip_ui,
    }
    with timer("report"):
        json_path, md_path = write_report(out_dir, results, paths=paths, timings=timer.timings,
                                          level_rows=level_rows, notes=notes, context=context)
    paths["report_json"] = str(json_path)
    paths["report_md"] = str(md_path)
    all_passed, summary = overall(results)
    if notes:
        summary += " | notes: " + " / ".join(notes)
    return {
        "all_passed": all_passed,
        "summary": summary,
        "results": {r.name: r.to_json() for r in results},
        "paths": jsonable(paths),
        "picked_level": picked,
        "notes": notes,
        "timings_s": timer.timings,
    }
