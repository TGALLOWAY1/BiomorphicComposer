"""Command-line front door. Each subcommand lazily imports its module so `--help` stays fast."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_ingest(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ingest", help="run the decomposition pipeline on a photo and write a session directory")
    p.add_argument("photo", type=Path)
    p.add_argument("--out", type=Path, default=None, help="session directory (default: work/<photo stem>)")
    p.add_argument("--working-width", type=int, default=None, help="max width of the working copy (default 1800)")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="override a pipeline parameter (repeatable)")
    p.add_argument("--debug", action="store_true", help="write debug images into <session>/debug/")


def _add_serve(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("serve", help="serve the review UI for a session")
    p.add_argument("session", type=Path)
    p.add_argument("--library", type=Path, default=Path("library"))
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from a phone on the same network")
    p.add_argument("--port", type=int, default=8765)


def _add_verify(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("verify", help="run the pipeline on a sample and write the verification report")
    p.add_argument("photo", type=Path)
    p.add_argument("--map", type=Path, required=True, help="hand-made segmentation map (ground truth)")
    p.add_argument("--out", type=Path, default=Path("verify_out"))
    p.add_argument("--labels", type=Path, default=None, help="JSON of map piece labels (default: next to the map)")
    p.add_argument("--working-width", type=int, default=None)
    p.add_argument("--skip-ui", action="store_true", help="skip the Playwright drive of the review UI")


def _add_library(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("library", help="inspect a library")
    p.add_argument("--library", type=Path, default=Path("library"))
    s = p.add_subparsers(dest="lib_cmd", required=True)
    s.add_parser("list", help="list tiles")
    t = s.add_parser("tag", help="add a tag to a tile")
    t.add_argument("tile_id")
    t.add_argument("tag")
    u = s.add_parser("untag", help="remove a tag from a tile")
    u.add_argument("tile_id")
    u.add_argument("tag")


def _add_sheet(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("sheet", help="write a debug contact sheet for a session")
    p.add_argument("session", type=Path)
    p.add_argument("--out", type=Path, default=None)


def _add_grouping(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("grouping", help="measure how many gestures the review UI needs per chunk of the artist's map")
    p.add_argument("session", type=Path)
    p.add_argument("--map", type=Path, required=True, help="hand-made segmentation map (ground truth)")
    p.add_argument("--labels", type=Path, default=None, help="JSON of map piece labels (default: next to the map)")
    p.add_argument("--out", type=Path, default=Path("grouping_out"))


def _add_pairs(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("pairs", help="print sides and mirror pairs at a level")
    p.add_argument("session", type=Path)
    p.add_argument("--level", type=int, required=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="biomorphic", description="Biomorphic Composer V1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for add in (_add_ingest, _add_serve, _add_verify, _add_library, _add_sheet, _add_pairs, _add_grouping):
        add(sub)
    return ap


def _parse_params(items: list[str]) -> dict:
    out = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "ingest":
        from biomorphic.pipeline.run import run_pipeline
        params = _parse_params(args.param)
        if args.working_width:
            params["working_width"] = args.working_width
        out = args.out or Path("work") / args.photo.stem
        path = run_pipeline(args.photo, out, params=params, debug=args.debug)
        print(f"session written to {path}")
        return 0
    if args.cmd == "serve":
        from biomorphic.server.app import serve
        serve(args.session, args.library, host=args.host, port=args.port)
        return 0
    if args.cmd == "verify":
        from biomorphic.verify.run import run_verify
        result = run_verify(args.photo, args.map, args.out, labels_path=args.labels,
                            working_width=args.working_width, skip_ui=args.skip_ui)
        print(result.get("summary", ""))
        return 0 if result.get("all_passed") else 1
    if args.cmd == "library":
        from biomorphic.library.db import Library
        lib = Library.open(args.library)
        if args.lib_cmd == "list":
            for t in lib.list_tiles():
                print(f"{t.id}\t{t.side}\t{t.area_px}px\tpair={t.pair_tile_id or '-'}\ttags={','.join(lib.tags(t.id)) or '-'}")
        elif args.lib_cmd == "tag":
            lib.add_tag(args.tile_id, args.tag)
        elif args.lib_cmd == "untag":
            lib.remove_tag(args.tile_id, args.tag)
        return 0
    if args.cmd == "sheet":
        from biomorphic.pipeline.debug import write_debug_sheet
        out = write_debug_sheet(args.session, args.out)
        print(f"sheet written to {out}")
        return 0
    if args.cmd == "grouping":
        from biomorphic.verify.grouping import run_grouping
        result = run_grouping(args.session, args.map, args.out, labels_path=args.labels)
        s = result["summary"]
        print(f"{s['one_region_pieces']}/{s['n_pieces']} chunks are one region at some level; "
              f"single-level gestures {s['single_level']['gestures']} (median {s['single_level']['median_gestures']:g}), "
              f"mixed {s['mixed']['gestures']}, with mirror co-select {s['mixed']['gestures_with_mirror_coselect']}")
        print(f"report written to {result['report_path']}")
        return 0
    if args.cmd == "pairs":
        from biomorphic.session import Session
        s = Session.load(args.session)
        p = s.pairing(args.level)
        sides = p["sides"]
        print(f"level {args.level}: {len(sides)} regions, "
              f"{sum(v == 'L' for v in sides.values())} L / {sum(v == 'R' for v in sides.values())} R / "
              f"{sum(v == 'axis' for v in sides.values())} axis, {len(p['pairs'])} pairs")
        for pr in sorted(p["pairs"], key=lambda q: -q["iou"]):
            print(f"  {pr['a']:>8} <-> {pr['b']:<8} iou={pr['iou']:.2f} dx={pr.get('dx', 0):+.1f} dy={pr.get('dy', 0):+.1f}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
