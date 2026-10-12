"""report.json and report.md for `biomorphic verify` (docs/ARCHITECTURE.md, section Verify).

report.md leads with a pass/fail table, then every check's numbers, the ground-truth table with its misses,
the per-level counts, the screenshot list and a "where the drawing disagrees with the thresholds" section
that quotes the measured numbers next to the thresholds. Checks that were not run are named explicitly;
`all_passed` ignores them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from biomorphic.verify.checks import CheckResult, jsonable

CHECK_TITLES = {
    "pipeline": "0. Pipeline run",
    "partition_integrity": "1. Partition integrity",
    "ink_fidelity": "2. Ink fidelity",
    "axis_check": "3. Axis",
    "granularity": "4. Granularity",
    "pairing_check": "5. Pairing",
    "ground_truth": "6. Ground truth",
    "non_destructive": "7. Non-destructive render modes",
    "provenance_immutable": "8. Provenance immutable",
    "ui_drive": "UI drive (Playwright)",
}


def overall(results: list[CheckResult]) -> tuple[bool, str]:
    """(all_passed, summary). all_passed ignores not-run checks; the summary names them."""
    failed = [r.name for r in results if r.passed is False]
    not_run = [r.name for r in results if r.passed is None]
    passed = [r.name for r in results if r.passed is True]
    all_passed = not failed
    parts = [f"{'PASS' if all_passed else 'FAIL'}: {len(passed)} passed"]
    if failed:
        parts.append(f"{len(failed)} failed ({', '.join(failed)})")
    if not_run:
        parts.append(f"{len(not_run)} not run ({', '.join(not_run)})")
    return all_passed, "; ".join(parts)


def _esc(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{100.0 * float(v):.1f} %"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(_esc(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_esc(_fmt(c)) for c in r) + " |")
    return out


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


# ----------------------------------------------------------------------------- per-check sections
def _section_numbers(r: CheckResult) -> list[str]:
    d = r.details
    lines: list[str] = []
    if r.passed is None:
        # not run: only the reason and whatever partial facts exist, no empty placeholders
        lines.append(f"- not run: {_esc(d.get('reason') or r.summary)}")
        for k in ("session_dir", "synthetic", "steps", "notes", "url"):
            if d.get(k):
                lines.append(f"- {k}: {_esc(d.get(k))}")
        return lines
    if r.name == "partition_integrity" and "levels" in d:
        lines += _table(["level", "regions", "cover ok", "cover mismatch px", "recomposite ok", "diff px", "gap px", "overlap px"],
                        [[x["index"], x["n_regions"], x["cover_ok"], x["cover_mismatch_px"], x["recomposite_ok"],
                          x["recomposite_diff_px"], x["gap_px"], x["overlap_px"]] for x in d["levels"]])
    elif r.name == "ink_fidelity" and "levels" in d:
        lines.append(f"Ink mask dilated by {d.get('dilate_px')} px; picked level {d.get('picked_level')}.")
        lines.append("")
        lines += _table(["level", "boundary px", "on ink px", "off ink px", "fraction on ink"],
                        [[x["index"], x["n_boundary_px"], x["on_ink_px"], x["off_ink_px"], _pct(x["fraction"])]
                         for x in d["levels"]])
    elif r.name == "axis_check":
        fb = d.get("figure_box_working") or {}
        lines += [f"- axis x = {_fmt(d.get('axis_x'), 1)} px, angle = {_fmt(d.get('angle_deg'), 2)} deg",
                  f"- figure box (working px): x={fb.get('x')} y={fb.get('y')} w={fb.get('w')} h={fb.get('h')}, "
                  f"centre x = {_fmt(d.get('figure_centre_x'), 1)}",
                  f"- offset from centre: {_fmt(d.get('offset_px'), 1)} px = {_pct(d.get('offset_fraction'))} of the "
                  f"figure width (allowed +/-{_pct((d.get('threshold') or {}).get('max_abs_offset_fraction'))})",
                  f"- pipeline axis.score = {_fmt(d.get('score'))}, axis.mirror_iou = {_fmt(d.get('mirror_iou'))}, "
                  f"mirror IoU recomputed by verify = {_fmt(d.get('recomputed_mirror_iou'))}"]
    elif r.name == "granularity":
        t = d.get("threshold", {})
        lines += [f"- target band: {t.get('total_min')}-{t.get('total_max')} regions, about {t.get('axis_target')} "
                  f"on-axis, at most {t.get('side_max')} per side",
                  f"- picked level: **{d.get('picked_level')}**; levels inside the band: {d.get('in_band_levels')}",
                  "- the per-level table is below."]
    elif r.name == "pairing_check":
        lines += [f"- level {d.get('level')}: {d.get('n_paired')} of {d.get('n_off_axis')} off-axis regions paired "
                  f"({_pct(d.get('fraction'))}), {d.get('n_axis')} on-axis regions"]
        unp = d.get("unpaired") or []
        if unp:
            lines.append("")
            lines.append(f"Unpaired regions ({len(unp)}), largest first:")
            lines.append("")
            lines += _table(["region", "side", "area px", "bbox x,y,w,h"],
                            [[u["id"], u["side"], u["area"], ",".join(str(v) for v in u.get("bbox", []))] for u in unp[:40]])
        prs = d.get("pairs") or []
        if prs:
            lines.append("")
            lines.append(f"Top pairs by IoU ({min(len(prs), 12)} of {len(prs)}):")
            lines.append("")
            lines += _table(["a", "b", "iou", "dx", "dy", "delta E"],
                            [[p.get("a"), p.get("b"), _fmt(p.get("iou")), _fmt(p.get("dx"), 1), _fmt(p.get("dy"), 1),
                              _fmt(p.get("delta_e"), 1)] for p in prs[:12]])
    elif r.name == "ground_truth":
        lines += [f"- map: `{d.get('map')}`; labels: `{d.get('labels_path')}` "
                  f"({'loaded' if d.get('labels_loaded') else d.get('labels_note') or 'not loaded'})",
                  f"- {d.get('n_pieces')} map pieces, {d.get('n_candidates')} candidate regions at level {d.get('level')}",
                  f"- matched {d.get('n_matched')} of {d.get('n_rows')} ({_pct(d.get('matched_fraction'))}); "
                  f"scores min/median/max = {_fmt(d.get('score_min'))} / {_fmt(d.get('score_median'))} / {_fmt(d.get('score_max'))}"]
        rows = d.get("rows") or []
        if rows:
            lines.append("")
            lines += _table(["map label", "tile (region id)", "score", "matched"],
                            [[x.get("label"), x.get("tile_id"), _fmt(x.get("score")), x.get("matched")] for x in rows])
        misses = d.get("misses") or []
        lines.append("")
        lines.append("Misses: " + (", ".join(f"{m.get('label')} (best r{m.get('tile_id')} @ {_fmt(m.get('score'))})"
                                             for m in misses) if misses else "none"))
    elif r.name == "non_destructive":
        lines.append(f"- render modes: {', '.join(d.get('modes') or [])}")
        tiles = d.get("tiles") or []
        if tiles:
            lines.append("")
            lines += _table(["tile", "sha256 before", "sha256 after", "api before == after", "identical"],
                            [[t["id"], t["sha_before"][:16] + "...", t["sha_after"][:16] + "...",
                              t["api_sha_before"] == t["api_sha_after"], t["identical"]] for t in tiles])
        lines.append(f"- files in tiles/ unchanged: {_fmt(d.get('no_new_files'))}")
    elif r.name == "provenance_immutable":
        lines += [f"- `provenance_is_immutable()` -> {_fmt(d.get('api_flag'))}",
                  f"- public Library members mentioning provenance: {d.get('public_provenance_members')}",
                  f"- trigger present in sqlite_master: {_fmt(d.get('trigger_present'))} ({d.get('trigger_names')})",
                  f"- direct `UPDATE provenance` on {d.get('n_rows')} rows raised IntegrityError: "
                  f"{_fmt(d.get('update_raised_integrity_error'))} ({d.get('update_error') or d.get('update_outcome')})"]
        if d.get("suspicious_methods"):
            lines.append(f"- methods with a provenance-looking parameter: {d.get('suspicious_methods')}")
    elif r.name == "ui_drive":
        for k in ("url", "picked_level", "selected", "twins", "toast_text", "saved_tile_ids", "provenance_ok"):
            if k in d:
                lines.append(f"- {k}: {_esc(d.get(k))}")
        if d.get("tap_target"):
            tt = d["tap_target"]
            lines.append(f"- tapped region {tt.get('region_id')} (side {tt.get('side')}, twin {tt.get('twin_id')}, "
                         f"iou {_fmt(tt.get('iou'))}) at image px {tt.get('image_xy')} -> page {d.get('tap_page_xy')}")
        for n in d.get("notes") or []:
            lines.append(f"- note: {n}")
    elif r.name == "pipeline":
        for k, v in d.items():
            if k not in ("traceback",):
                lines.append(f"- {k}: {_esc(v)}")
    return lines


def _disagreements(results: list[CheckResult]) -> list[str]:
    lines: list[str] = []
    failed = [r for r in results if r.passed is False]
    if not failed:
        lines.append("Every check that ran met its threshold.")
    for r in failed:
        d = r.details
        lines.append(f"### {CHECK_TITLES.get(r.name, r.name)}")
        lines.append("")
        lines.append(f"- threshold: `{jsonable(d.get('threshold'))}`")
        lines.append(f"- measured: `{jsonable(d.get('measured'))}`")
        lines.append(f"- {r.summary}")
        lines.append("")
    not_run = [r for r in results if r.passed is None]
    if not_run:
        lines.append("### Not run")
        lines.append("")
        for r in not_run:
            lines.append(f"- **{r.name}**: {_esc(r.summary)}")
    return lines


# ----------------------------------------------------------------------------- writers
def write_report(out_dir: Path | str, results: list[CheckResult], *, paths: dict, timings: dict,
                 level_rows: list[dict], notes: list[str], context: dict) -> tuple[Path, Path]:
    """Write report.json and report.md into out_dir; returns their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_passed, summary = overall(results)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "generated_at": now,
        "all_passed": all_passed,
        "summary": summary,
        "context": jsonable(context),
        "checks": [r.to_json() for r in results],
        "levels": jsonable(level_rows),
        "paths": jsonable(paths),
        "timings_s": jsonable(timings),
        "notes": list(notes),
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(payload, indent=1))

    md: list[str] = []
    md.append("# Biomorphic Composer - verification report")
    md.append("")
    md.append(f"Generated {now}. Photo `{context.get('photo')}`, map `{context.get('map')}`, "
              f"session `{context.get('session_dir')}` (id {context.get('session_id')}), "
              f"pipeline version {context.get('pipeline_version')}.")
    md.append("")
    for n in notes:
        md.append(f"> **{n}**")
        md.append("")
    md.append(f"## Summary: {summary}")
    md.append("")
    md += _table(["check", "name", "status", "summary"],
                 [[CHECK_TITLES.get(r.name, r.name), f"`{r.name}`", r.status, r.summary] for r in results])
    md.append("")

    for r in results:
        md.append(f"## {CHECK_TITLES.get(r.name, r.name)} (`{r.name}`) - {r.status}")
        md.append("")
        md.append(r.summary)
        md.append("")
        md += _section_numbers(r)
        md.append("")

    md.append("## Per-level counts")
    md.append("")
    if level_rows:
        md += _table(["level", "merges", "regions", "on-axis", "left", "right", "pairs", "paired", "ink fidelity",
                      "integrity", "band distance", "in band", "picked"],
                     [[x["index"], x.get("n_merges"), x.get("n_regions"), x.get("n_axis"), x.get("n_left"), x.get("n_right"),
                       x.get("n_pairs"), _pct(x.get("paired_fraction")), _pct(x.get("ink_fraction")),
                       x.get("integrity_ok"), _fmt(x.get("distance"), 2), x.get("in_band"),
                       "<--" if x.get("picked") else ""] for x in level_rows])
    else:
        md.append("(no level table: the granularity check did not run)")
    md.append("")

    md.append("## Files")
    md.append("")
    for k, v in paths.items():
        if k == "screenshots":
            continue
        md.append(f"- {k}: `{v}`")
    shots = paths.get("screenshots") or {}
    md.append("")
    md.append("### UI screenshots")
    md.append("")
    if shots:
        for k, v in shots.items():
            md.append(f"- {k}: `{v}`")
    else:
        md.append("(none: the UI drive was skipped or could not run)")
    md.append("")

    md.append("## Where the drawing disagrees with the thresholds")
    md.append("")
    md += _disagreements(results)
    md.append("")

    md.append("## Timings")
    md.append("")
    md += _table(["stage", "seconds"], [[k, _fmt(v, 2)] for k, v in timings.items()])
    md.append("")
    md_path = out_dir / "report.md"
    md_path.write_text("\n".join(md))
    return json_path, md_path
