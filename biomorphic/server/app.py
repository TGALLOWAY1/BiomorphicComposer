"""Minimal local server for the review UI (docs/ARCHITECTURE.md, section Server + UI).

stdlib only: `ThreadingHTTPServer` + `json`. One `ReviewState` holds the `Session`, the `Library` (or
None when the library is unavailable) and small PNG caches; a single lock serialises every API call
because `Session` caches are plain dicts. Static files (the self-contained page) are served from
`static/`. Every error is JSON with a proper status code.

    serve(session_dir, library_dir, host, port)   blocks until Ctrl-C
    make_server(...) -> ReviewServer              for tests (port 0 = pick a free port)
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np

from biomorphic import tiles as tile_utils
from biomorphic.session import GROUP_ID_BASE, Session

log = logging.getLogger("biomorphic.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"
INK_RGBA = (22, 33, 49, 255)                      # #162131, the reel's ink colour
# Region boundaries sit on the ink by construction, so an ink-coloured overlay is invisible on the
# drawing. Magenta reads on blue, green and grey fills and is distinct from the cyan selection and
# the orange twin highlight.
BOUNDARY_RGBA = (214, 51, 171, 255)
PAPER_VEIL_RGBA = (243, 246, 249, 200)            # drawn over everything outside the figure ("mask out the paper")
INK_RGBA_OVERLAY = (255, 196, 64, 235)            # the ink mask as the reel's amber "walls"
AXIS_CURVE_POINTS = 160                           # samples of the axis-search curve sent to the page
THUMB_HEIGHT = 240
RENDER_MODES = ("original", "greyscale", "outline", "remap")
TILE_ID_RE = re.compile(r"^(?P<stem>.+)-(?P<session>[0-9a-f]{8})-L(?P<level>\d+)-r(?P<region>\d+)$")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".json": "application/json", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
                 ".woff2": "font/woff2"}
# Demo palette for `?mode=remap` when the caller gives none: the reel's cool -> warm recolour
# (blues -> brick red, teal -> red-orange, mint -> ochre, grey -> warm grey), in CIE Lab.
DEMO_PALETTE = [
    {"from": [45, 10, -45], "to": [45, 45, 30]},
    {"from": [60, -25, -12], "to": [60, 45, 45]},
    {"from": [85, -18, 12], "to": [78, 8, 50]},
    {"from": [70, 0, 0], "to": [70, 4, 12]},
]


def _lab_hex(lab: list[float]) -> str:
    """CIE Lab -> '#rrggbb' (clipped to sRGB), for the palette swatches."""
    import cv2
    rgb = cv2.cvtColor(np.asarray([[lab]], np.float32), cv2.COLOR_LAB2RGB)[0, 0]
    r, g, b = (int(v) for v in np.clip(np.rint(rgb * 255.0), 0, 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def demo_palette_swatches() -> list[dict]:
    return [{"from": _lab_hex(e["from"]), "to": _lab_hex(e["to"])} for e in DEMO_PALETTE]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _field(rec: Any, name: str, default: Any = None) -> Any:
    """Read a field from a TileRecord whether it is a dataclass, an object or a dict."""
    if isinstance(rec, dict):
        return rec.get(name, default)
    return getattr(rec, name, default)


def _accepts(fn: Callable, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


# ----------------------------------------------------------------------------- state
class ReviewState:
    """What the handlers share: session, library (or None) and caches, behind one lock."""

    def __init__(self, session_dir: Path | str, library_dir: Path | str):
        self.session_dir = Path(session_dir)
        self.library_dir = Path(library_dir)
        self.session = Session.load(self.session_dir)
        self.lock = threading.RLock()
        self._png_cache: dict[tuple, bytes] = {}
        self.library: Any = None
        self.library_error: str | None = None
        self.open_library()

    # ---- library (degrades to None) ------------------------------------------------------
    def open_library(self) -> None:
        try:
            from biomorphic.library.db import Library
            self.library = Library.open(self.library_dir)
            self.library_error = None
        except NotImplementedError as exc:
            self.library, self.library_error = None, f"library not implemented: {exc}"
        except Exception as exc:  # noqa: BLE001 - the review page must still work without a library
            self.library, self.library_error = None, f"library unavailable: {type(exc).__name__}: {exc}"
        if self.library is None:
            log.warning("%s (saving is disabled)", self.library_error)

    def _require_library(self) -> Any:
        if self.library is None:
            raise ApiError(503, self.library_error or "library unavailable")
        return self.library

    def library_summary(self) -> dict | None:
        if self.library is None:
            return None
        try:
            if hasattr(self.library, "summary"):
                summary = self.library.summary()
                summary = dict(summary) if isinstance(summary, dict) else {"summary": summary}
            else:
                summary = {}
            if "tile_count" not in summary:
                summary["tile_count"] = int(summary["n_tiles"]) if "n_tiles" in summary else len(self._list_tiles())
            summary.setdefault("dir", str(self.library_dir))
            return summary
        except Exception as exc:  # noqa: BLE001
            log.warning("library summary failed: %s", exc)
            return {"dir": str(self.library_dir), "error": str(exc)}

    def _list_tiles(self) -> list:
        return list(self.library.list_tiles()) if self.library is not None else []

    def _saved_index(self) -> dict[tuple[int, int], str]:
        """(level, region_id) -> tile id for every tile of this session already in the library."""
        if self.library is None:
            return {}
        try:
            tiles = self._list_tiles()
        except Exception as exc:  # noqa: BLE001
            log.warning("list_tiles failed: %s", exc)
            return {}
        out: dict[tuple[int, int], str] = {}
        sid = self.session.session_id
        for rec in tiles:
            m = TILE_ID_RE.match(str(_field(rec, "id") or ""))
            if m and m["session"] == sid:
                out[(int(m["level"]), int(m["region"]))] = m.group(0)
        return out

    def _tile_names(self) -> dict[str, str]:
        try:
            return {str(_field(r, "id")): _field(r, "name") for r in self._list_tiles()}
        except Exception:  # noqa: BLE001
            return {}

    def _tile_json(self, rec: Any, tags: dict[str, list[str]] | None = None) -> dict:
        tid = str(_field(rec, "id"))
        members = [str(m) for m in (_field(rec, "members", []) or [])]
        prov = _field(rec, "provenance", {}) or {}
        if tags is None:
            try:
                tag_list = list(self.library.tags(tid)) if self.library is not None else []
            except Exception:  # noqa: BLE001
                tag_list = []
        else:
            tag_list = tags.get(tid, [])
        filename = prov.get("source_filename")
        return {
            "id": tid, "name": _field(rec, "name"), "png_url": f"/api/tiles/{tid}.png", "side": _field(rec, "side"),
            "pair_tile_id": _field(rec, "pair_tile_id"), "pair_iou": _field(rec, "pair_iou"),
            "area_px": _field(rec, "area_px"), "width": _field(rec, "width"), "height": _field(rec, "height"),
            "created_at": _field(rec, "created_at"),
            "members": members, "composite": bool(members), "tags": tag_list,
            # provenance, read-only: where the part came from
            "source": {"sha256": prov.get("source_sha256"), "filename": filename,
                       "stem": Path(str(filename)).stem if filename else None},
            "session_id": prov.get("session_id"), "level_index": prov.get("level_index"),
            "region_id": prov.get("region_id"), "pipeline_version": prov.get("pipeline_version"),
            "bbox": [prov.get(k) for k in ("bbox_x", "bbox_y", "bbox_w", "bbox_h")] if prov else None,
            "working": [prov.get("working_w"), prov.get("working_h")] if prov else None,
            "in_session": prov.get("session_id") == self.session.session_id,
        }

    @staticmethod
    def _tile_level(level: int, rid: int) -> int:
        """A composite is the same region at every level (pinned), so its tile lives at level 0."""
        return 0 if rid >= GROUP_ID_BASE else int(level)

    # ---- validation helpers --------------------------------------------------------------
    def _check_level(self, level: Any) -> int:
        try:
            i = int(level)
        except (TypeError, ValueError):
            raise ApiError(400, f"level must be an integer, got {level!r}") from None
        if not 0 <= i < len(self.session.levels):
            raise ApiError(404, f"level {i} out of range 0..{len(self.session.levels) - 1}")
        return i

    @staticmethod
    def _parse_ids(region_ids: Any) -> list[int]:
        if not isinstance(region_ids, (list, tuple)):
            raise ApiError(400, "region_ids must be a list")
        out: list[int] = []
        for r in region_ids:
            try:
                v = int(r)
            except (TypeError, ValueError):
                raise ApiError(400, f"bad region id {r!r}") from None
            if v not in out:
                out.append(v)
        return out

    def _pair_info(self, level: int) -> tuple[dict, dict, dict]:
        pairing = self.session.pairing(level)
        iou: dict[int, float] = {}
        for pr in pairing["pairs"]:
            iou[int(pr["a"])] = iou[int(pr["b"])] = float(pr["iou"])
        return pairing["sides"], pairing["twin"], iou

    # ---- JSON ----------------------------------------------------------------------------
    def session_json(self) -> dict:
        with self.lock:
            s = self.session
            ys, xs = np.nonzero(s.figure_mask)
            box = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)] \
                if xs.size else [0, 0, s.width, s.height]
            curve = s.axis_curve
            step = max(1, len(curve) // 80)
            return {"meta": s.meta, "groups": s.groups, "library": self.library_summary(),
                    "library_dir": str(self.library_dir), "n_leaves": s.tree.n_leaves,
                    # what the capture and axis screens draw (working px)
                    "figure_box": box, "figure_area": int(xs.size),
                    "axis_curve": [[round(float(y), 1), round(float(x), 2)] for y, x in curve[::step]],
                    "has_fold": s.fold is not None, "palette": demo_palette_swatches()}

    def axis_curve_json(self) -> dict:
        """The axis search as a curve: the mirror score (IoU of the figure with its reflection) for candidate
        axes across the search range. Recomputed from the figure mask (one FFT) and cached."""
        with self.lock:
            key = ("axis_curve",)
            if key not in self._png_cache:
                from biomorphic.pipeline import axis as axis_mod
                s = self.session
                prof = axis_mod.iou_profile(s.figure_mask)
                search = (s.meta.get("axis") or {}).get("search") or {}
                lo = float(search.get("x_min", s.width * 0.2))
                hi = float(search.get("x_max", s.width * 0.8))
                lo, hi = max(0.5, min(lo, hi)), min(float(s.width) - 0.5, max(lo, hi))
                ks = np.unique(np.clip(np.rint(np.linspace(lo, hi, AXIS_CURVE_POINTS) * 2.0).astype(int),
                                       1, len(prof) - 1))
                k_axis = int(np.clip(round(s.axis_x * 2.0), 1, len(prof) - 1))
                ks = np.unique(np.append(ks, k_axis))                    # the peak itself is always a sample
                self._png_cache[key] = json.dumps({
                    "points": [[k / 2.0, round(float(prof[k]), 4)] for k in ks.tolist()],
                    "x_min": lo, "x_max": hi, "axis_x": s.axis_x, "score": round(float(prof[k_axis]), 4),
                    "measure": "IoU of the figure with its mirror image"}).encode("utf-8")
            return json.loads(self._png_cache[key])

    def tree_json(self) -> dict:
        with self.lock:
            return self.session.tree.to_json()

    def regions_json(self, level: Any) -> dict:
        with self.lock:
            i = self._check_level(level)
            regs = self.session.regions(i)
            sides, twin, iou = self._pair_info(i)
            saved = self._saved_index()
            names = self._tile_names()
            out: dict[str, dict] = {}
            for rid, info in regs.items():
                t = twin.get(rid)
                out[str(rid)] = {
                    "area": int(info.area), "bbox": [int(v) for v in info.bbox],
                    "centroid": [round(float(info.centroid[0]), 2), round(float(info.centroid[1]), 2)],
                    "side": sides.get(rid), "twin": int(t) if t is not None else None,
                    "iou": round(iou[rid], 4) if rid in iou else None,
                    "saved_tile_id": saved.get((self._tile_level(i, rid), rid)),
                    "saved_name": names.get(saved.get((self._tile_level(i, rid), rid))),
                    "group_index": rid - GROUP_ID_BASE if rid >= GROUP_ID_BASE else None,
                }
            return out

    def merge(self, level: Any, region_ids: Any, mirror: bool = True) -> dict:
        with self.lock:
            s = self.session
            i = self._check_level(level)
            ids = self._parse_ids(region_ids)
            if len(ids) < 2:
                raise ApiError(400, "merge needs at least two regions")
            regs = s.regions(i)
            missing = [r for r in ids if r not in regs]
            if missing:
                raise ApiError(404, f"no region {missing[0]} at level {i}")
            leaves = sorted({leaf for r in ids for leaf in regs[r].leaves})
            mirror_leaves: list[int] | None = None
            if mirror:
                # the mirror of the composite as a whole, found on the fold: the parts under the folded
                # composite, whether or not each tapped part had a twin of its own (empty when the
                # composite straddles the axis and is its own mirror)
                mirror_leaves = s.mirror_leaves(leaves) or None
            created: list[int] = []
            s.add_group(leaves)
            created.append(int(s.leaf_to_region(i)[leaves[0]]))
            if mirror_leaves:
                s.add_group(mirror_leaves)
                created.append(int(s.leaf_to_region(i)[mirror_leaves[0]]))
            self._png_cache.clear()
            return {"groups": s.groups, "created": created}

    def unmerge(self, group_index: Any) -> dict:
        with self.lock:
            try:
                k = int(group_index)
            except (TypeError, ValueError):
                raise ApiError(400, "group_index must be an integer") from None
            groups = self.session.groups
            if not 0 <= k < len(groups):
                raise ApiError(404, f"no group {k} (there are {len(groups)})")
            self.session.remove_group(k)
            self._png_cache.clear()
            return {"groups": self.session.groups, "removed": k}

    def save(self, level: Any, region_ids: Any) -> dict:
        with self.lock:
            lib = self._require_library()
            i = self._check_level(level)
            ids = self._parse_ids(region_ids)
            if not ids:
                raise ApiError(400, "nothing to save")
            regs = self.session.regions(i)
            missing = [r for r in ids if r not in regs]
            if missing:
                raise ApiError(404, f"no region {missing[0]} at level {i}")
            _sides, twin, iou = self._pair_info(i)
            saved = self._saved_index()
            records: dict[int, Any] = {}
            for rid in ids:
                if rid in records:
                    continue
                t = twin.get(rid)
                if t is not None and t in ids and t not in records:
                    records[rid], records[t] = self._save_pair(lib, i, rid, t, iou.get(rid), saved)
                else:
                    # a twin saved earlier on its own still gets linked
                    partner = saved.get((self._tile_level(i, t), t)) if t is not None else None
                    records[rid] = self._save_one(lib, self._tile_level(i, rid), rid, saved, partner, iou.get(rid))
            return {"tiles": [self._tile_json(records[r]) for r in ids]}

    def _existing(self, lib: Any, level: int, rid: int, saved: dict) -> Any:
        tid = saved.get((level, rid))
        if tid is None:
            return None
        try:
            return lib.get(tid)
        except (KeyError, LookupError):
            return None

    def _save_one(self, lib: Any, level: int, rid: int, saved: dict,
                  partner: str | None = None, iou: float | None = None) -> Any:
        """Save one region (or return the tile that already exists); link it to `partner` (a tile id) if given."""
        rec = self._existing(lib, level, rid, saved)
        if rec is None:
            # a partner that is already stored is linked in the same step, so the new tile takes its mirrored name
            kw = {"pair_with": partner, "pair_iou": iou} if partner is not None else {}
            if rid >= GROUP_ID_BASE and hasattr(lib, "save_composite"):
                rec = lib.save_composite(self.session, level, rid, **kw)   # the composite and its components
            else:
                rec = lib.save_tile(self.session, level, rid, **kw)
            saved[(level, rid)] = str(_field(rec, "id"))
        tid = str(_field(rec, "id"))
        if partner is not None and partner != tid and _field(rec, "pair_tile_id") != partner:
            lib.link_pair(tid, partner, iou)
            rec = lib.get(tid) or rec
        return rec

    def _save_pair(self, lib: Any, level: int, a: int, b: int, iou: float | None, saved: dict) -> tuple[Any, Any]:
        la, lb = self._tile_level(level, a), self._tile_level(level, b)
        if a >= GROUP_ID_BASE or b >= GROUP_ID_BASE:         # a composite in the pair: save each (with its
            # components), then link
            ra = self._save_one(lib, la, a, saved)
            rb = self._save_one(lib, lb, b, saved, str(_field(ra, "id")), iou)
            return lib.get(str(_field(ra, "id"))) or ra, rb
        ra = self._existing(lib, level, a, saved)
        rb = self._existing(lib, level, b, saved)
        if ra is None and rb is None and hasattr(lib, "save_pair"):
            kw = {"iou": iou} if _accepts(lib.save_pair, "iou") else {}
            res = lib.save_pair(self.session, level, a, b, **kw)
            if isinstance(res, (tuple, list)) and len(res) == 2:
                ra, rb = res
            else:
                ra, rb = self._refetch(lib, level, a, b)
            saved[(level, a)], saved[(level, b)] = str(_field(ra, "id")), str(_field(rb, "id"))
            return ra, rb
        ra = self._save_one(lib, level, a, saved)
        rb = self._save_one(lib, level, b, saved, str(_field(ra, "id")), iou)
        return lib.get(str(_field(ra, "id"))) or ra, rb

    def _refetch(self, lib: Any, level: int, a: int, b: int) -> tuple[Any, Any]:
        saved = self._saved_index()
        return lib.get(saved[(level, a)]), lib.get(saved[(level, b)])

    def library_json(self) -> dict:
        with self.lock:
            if self.library is None:
                return {"tiles": []}
            tags = self.library.all_tags() if hasattr(self.library, "all_tags") else None
            recs = self._list_tiles()
            names = {str(_field(r, "id")): _field(r, "name") for r in recs}
            tiles = [self._tile_json(r, tags) for r in recs]
            for t in tiles:
                t["pair_name"] = names.get(t["pair_tile_id"]) if t["pair_tile_id"] else None
            tiles.sort(key=lambda t: (str(t.get("created_at") or ""), t["id"]))
            return {"tiles": tiles}

    def sources_json(self) -> dict:
        """Every drawing the library knows, plus the one under review here: the reel's Sources card."""
        with self.lock:
            cur = self.session.meta.get("source", {})
            cur_sha = cur.get("sha256")
            rows = list(self.library.sources()) if self.library is not None and hasattr(self.library, "sources") else []
            if cur_sha not in {r["sha256"] for r in rows}:
                rows.append({"sha256": cur_sha, "filename": cur.get("filename"), "width": cur.get("width"),
                             "height": cur.get("height"), "first_seen_at": None, "n_tiles": 0})
            out = []
            for r in rows:
                current = r["sha256"] == cur_sha
                filename = r.get("filename") or "source"
                out.append({**r, "stem": Path(str(filename)).stem, "current": current,
                            "status": "in_library" if r["n_tiles"] else "waiting",
                            "n_fragments": int(self.session.tree.n_leaves) if current else None,
                            "thumb_url": f"/api/sources/{r['sha256']}/thumb.png"})
            return {"sources": out}

    def tile_tags(self, tile_id: str, body: dict) -> dict:
        with self.lock:
            lib = self._require_library()
            add, remove = body.get("add"), body.get("remove")
            if (add is None) == (remove is None):
                raise ApiError(400, "send exactly one of add or remove")
            tag = str(add if add is not None else remove).strip().lstrip("#").strip()
            if not tag or len(tag) > 32:
                raise ApiError(400, "a tag is 1-32 characters")
            try:
                (lib.add_tag if add is not None else lib.remove_tag)(tile_id, tag)
            except KeyError:
                raise ApiError(404, f"no tile {tile_id}") from None
            return {"id": tile_id, "tags": list(lib.tags(tile_id))}

    def tile_rename(self, tile_id: str, body: dict) -> dict:
        with self.lock:
            lib = self._require_library()
            try:
                rec = lib.rename(tile_id, str(body.get("name", "")))
            except KeyError:
                raise ApiError(404, f"no tile {tile_id}") from None
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
            return self._tile_json(rec)

    # ---- PNGs ----------------------------------------------------------------------------
    def working_png(self) -> bytes:
        with self.lock:
            key = ("working",)
            if key not in self._png_cache:
                self._png_cache[key] = tile_utils.encode_png(self.session.working_rgb)
            return self._png_cache[key]

    def labels_png(self, level: Any) -> bytes:
        with self.lock:
            i = self._check_level(level)
            key = ("labels", i, self.session._groups_key())
            if key not in self._png_cache:
                lab = self.session.label_map(i)
                rgb = np.empty(lab.shape + (3,), np.uint8)
                rgb[..., 0] = lab & 255
                rgb[..., 1] = (lab >> 8) & 255
                rgb[..., 2] = (lab >> 16) & 255
                self._png_cache[key] = tile_utils.encode_png(rgb)
            return self._png_cache[key]

    def overlay_png(self, level: Any) -> bytes:
        with self.lock:
            i = self._check_level(level)
            key = ("overlay", i, self.session._groups_key())
            if key not in self._png_cache:
                self._png_cache[key] = tile_utils.encode_png(boundary_overlay(self.session.label_map(i)))
            return self._png_cache[key]

    def figure_png(self) -> bytes:
        """RGBA veil over everything that is not the figure: the "mask out the paper" step, made visible."""
        with self.lock:
            key = ("figure",)
            if key not in self._png_cache:
                out = np.zeros(self.session.figure_mask.shape + (4,), np.uint8)
                out[~self.session.figure_mask] = PAPER_VEIL_RGBA
                self._png_cache[key] = tile_utils.encode_png(out)
            return self._png_cache[key]

    def ink_png(self) -> bytes:
        """RGBA overlay of the ink mask: the only walls the split respects."""
        with self.lock:
            key = ("ink",)
            if key not in self._png_cache:
                out = np.zeros(self.session.ink_mask.shape + (4,), np.uint8)
                out[self.session.ink_mask] = INK_RGBA_OVERLAY
                self._png_cache[key] = tile_utils.encode_png(out)
            return self._png_cache[key]

    def source_thumb_png(self, sha: str) -> bytes:
        """A small picture of a source drawing: the working copy for the session under review, else the
        drawing's stored tiles put back where they were cut (the library keeps tiles, not photos)."""
        import cv2
        with self.lock:
            s = self.session
            if sha == s.meta.get("source", {}).get("sha256"):
                key: tuple = ("thumb", sha)
                if key not in self._png_cache:
                    w = max(1, round(s.width * THUMB_HEIGHT / s.height))
                    small = cv2.resize(s.working_rgb, (w, THUMB_HEIGHT), interpolation=cv2.INTER_AREA)
                    self._png_cache[key] = tile_utils.encode_png(np.ascontiguousarray(small))
                return self._png_cache[key]
            lib = self._require_library()
            recs = [r for r in lib.list_tiles(source_sha256=sha) if not _field(r, "members")]
            if not recs:
                raise ApiError(404, f"no source {sha}")
            key = ("thumb", sha, len(recs))
            if key not in self._png_cache:
                prov0 = _field(recs[0], "provenance", {}) or {}
                ww, wh = int(prov0.get("working_w") or 1), int(prov0.get("working_h") or 1)
                k = THUMB_HEIGHT / wh
                canvas = np.zeros((THUMB_HEIGHT, max(1, round(ww * k)), 4), np.uint8)
                for r in recs:
                    prov = _field(r, "provenance", {}) or {}
                    try:
                        rgba = tile_utils.decode_png(lib.tile_png_bytes(str(_field(r, "id"))))
                    except (KeyError, FileNotFoundError):
                        continue
                    tw, th = max(1, round(rgba.shape[1] * k)), max(1, round(rgba.shape[0] * k))
                    small = cv2.resize(rgba, (tw, th), interpolation=cv2.INTER_AREA)
                    x, y = round(int(prov.get("bbox_x") or 0) * k), round(int(prov.get("bbox_y") or 0) * k)
                    x1, y1 = min(canvas.shape[1], x + tw), min(canvas.shape[0], y + th)
                    if x1 <= x or y1 <= y:
                        continue
                    part = small[:y1 - y, :x1 - x]
                    on = part[..., 3] > 127
                    canvas[y:y1, x:x1][on] = part[on]
                self._png_cache[key] = tile_utils.encode_png(canvas)
            return self._png_cache[key]

    def tile_png(self, tile_id: str, mode: str, palette_json: str | None) -> bytes:
        with self.lock:
            lib = self._require_library()
            if mode not in RENDER_MODES:
                raise ApiError(400, f"mode must be one of {', '.join(RENDER_MODES)}")
            try:
                data = lib.tile_png_bytes(tile_id)
            except (KeyError, LookupError, FileNotFoundError):
                raise ApiError(404, f"no tile {tile_id}") from None
            if mode == "original":
                return bytes(data)
            from biomorphic.library import render as render_mod
            render = getattr(render_mod, "render", None)
            if render is None:
                raise ApiError(501, "render modes are not implemented yet")
            rgba = tile_utils.decode_png(bytes(data))
            if mode == "remap":
                try:
                    palette = json.loads(palette_json) if palette_json else DEMO_PALETTE
                except ValueError:
                    raise ApiError(400, "palette must be JSON") from None
                try:
                    out = render(rgba, mode, palette)
                except ValueError as exc:
                    raise ApiError(400, str(exc)) from None
            else:
                out = render(rgba, mode)
            return tile_utils.encode_png(np.ascontiguousarray(out))


def boundary_overlay(label_map: np.ndarray, rgba: tuple[int, int, int, int] = BOUNDARY_RGBA) -> np.ndarray:
    """RGBA HxWx4: 2-px region boundaries in `rgba`, transparent elsewhere.

    A boundary pixel is one whose right or lower neighbour has a different label; both sides of
    such an edge are marked, which makes every line 2 px wide. Boundaries against the background
    (the figure silhouette) are included.
    """
    lab = np.asarray(label_map)
    edge = np.zeros(lab.shape, bool)
    dx = lab[:, 1:] != lab[:, :-1]
    edge[:, :-1] |= dx
    edge[:, 1:] |= dx
    dy = lab[1:, :] != lab[:-1, :]
    edge[:-1, :] |= dy
    edge[1:, :] |= dy
    out = np.zeros(lab.shape + (4,), np.uint8)
    out[edge] = rgba
    return out


# ----------------------------------------------------------------------------- HTTP
class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    state: ReviewState
    quiet: bool = False

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


class ReviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ReviewServer

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 - BaseHTTPRequestHandler API
        if not getattr(self.server, "quiet", False):
            log.info("%s %s", self.address_string(), fmt % args)

    # ---- plumbing --------------------------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        # read the whole body up front: a 404/405 answered with the body still unread would leave those bytes
        # to be parsed as the next request on a kept-alive connection
        length = int(self.headers.get("Content-Length") or 0)
        self._raw_body = self.rfile.read(length) if length > 0 else b""
        try:
            for route_method, pattern, handler in ROUTES:
                m = pattern.match(path)
                if m is None:
                    continue
                if route_method != method:
                    raise ApiError(405, f"{method} not allowed on {path}")
                handler(self, m, query)
                return
            raise ApiError(404, f"no route {path}")
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - never let a handler kill the server
            log.error("500 on %s %s\n%s", method, path, traceback.format_exc())
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _body(self) -> dict:
        raw = getattr(self, "_raw_body", b"")
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "body must be JSON") from None
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        return body

    def _send(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(json.dumps(obj).encode("utf-8"), "application/json", status)

    def _png(self, data: bytes) -> None:
        self._send(data, "image/png")

    # ---- static --------------------------------------------------------------------------
    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve()) + "/") and target != STATIC_DIR.resolve():
            raise ApiError(404, "not found")
        if not target.is_file():
            raise ApiError(404, f"no static file {name}")
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        cache = "public, max-age=604800" if target.suffix.lower() == ".woff2" else "no-cache"
        self._send(target.read_bytes(), ctype, cache=cache)

    def r_index(self, m: re.Match, q: dict) -> None:
        self._static("index.html")

    def r_static(self, m: re.Match, q: dict) -> None:
        self._static(m["name"])

    # ---- api -----------------------------------------------------------------------------
    def r_session(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.session_json())

    def r_working(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.working_png())

    def r_labels(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.labels_png(m["level"]))

    def r_overlay(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.overlay_png(m["level"]))

    def r_regions(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.regions_json(m["level"]))

    def r_merge(self, m: re.Match, q: dict) -> None:
        body = self._body()
        self._json(self.server.state.merge(body.get("level"), body.get("region_ids", []),
                                           bool(body.get("mirror", True))))

    def r_unmerge(self, m: re.Match, q: dict) -> None:
        body = self._body()
        self._json(self.server.state.unmerge(body.get("group_index")))

    def r_save(self, m: re.Match, q: dict) -> None:
        body = self._body()
        self._json(self.server.state.save(body.get("level"), body.get("region_ids", [])))

    def r_library(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.library_json())

    def r_axis_curve(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.axis_curve_json())

    def r_tree(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.tree_json())

    def r_figure(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.figure_png())

    def r_ink(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.ink_png())

    def r_sources(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.sources_json())

    def r_source_thumb(self, m: re.Match, q: dict) -> None:
        self._png(self.server.state.source_thumb_png(m["sha"]))

    def r_tile_tags(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.tile_tags(m["id"], self._body()))

    def r_tile_name(self, m: re.Match, q: dict) -> None:
        self._json(self.server.state.tile_rename(m["id"], self._body()))

    def r_tile(self, m: re.Match, q: dict) -> None:
        mode = (q.get("mode") or ["original"])[0]
        palette = (q.get("palette") or [None])[0]
        self._png(self.server.state.tile_png(m["id"], mode, palette))


ROUTES: list[tuple[str, re.Pattern, Callable]] = [
    ("GET", re.compile(r"^/$"), ReviewHandler.r_index),
    ("GET", re.compile(r"^/index\.html$"), ReviewHandler.r_index),
    ("GET", re.compile(r"^/static/(?P<name>[^/]+)$"), ReviewHandler.r_static),
    ("GET", re.compile(r"^/api/session$"), ReviewHandler.r_session),
    ("GET", re.compile(r"^/api/working\.png$"), ReviewHandler.r_working),
    ("GET", re.compile(r"^/api/levels/(?P<level>-?\d+)/labels\.png$"), ReviewHandler.r_labels),
    ("GET", re.compile(r"^/api/levels/(?P<level>-?\d+)/overlay\.png$"), ReviewHandler.r_overlay),
    ("GET", re.compile(r"^/api/levels/(?P<level>-?\d+)/regions$"), ReviewHandler.r_regions),
    ("POST", re.compile(r"^/api/merge$"), ReviewHandler.r_merge),
    ("POST", re.compile(r"^/api/unmerge$"), ReviewHandler.r_unmerge),
    ("POST", re.compile(r"^/api/save$"), ReviewHandler.r_save),
    ("GET", re.compile(r"^/api/library$"), ReviewHandler.r_library),
    ("GET", re.compile(r"^/api/axis/curve$"), ReviewHandler.r_axis_curve),
    ("GET", re.compile(r"^/api/tree$"), ReviewHandler.r_tree),
    ("GET", re.compile(r"^/api/figure\.png$"), ReviewHandler.r_figure),
    ("GET", re.compile(r"^/api/ink\.png$"), ReviewHandler.r_ink),
    ("GET", re.compile(r"^/api/sources$"), ReviewHandler.r_sources),
    ("GET", re.compile(r"^/api/sources/(?P<sha>[0-9a-f]{8,64})/thumb\.png$"), ReviewHandler.r_source_thumb),
    ("POST", re.compile(r"^/api/tiles/(?P<id>[^/]+)/tags$"), ReviewHandler.r_tile_tags),
    ("POST", re.compile(r"^/api/tiles/(?P<id>[^/]+)/name$"), ReviewHandler.r_tile_name),
    ("GET", re.compile(r"^/api/tiles/(?P<id>[^/]+)\.png$"), ReviewHandler.r_tile),
]


# ----------------------------------------------------------------------------- entry points
def make_server(session_dir: Path | str, library_dir: Path | str, host: str = "127.0.0.1", port: int = 8765,
                *, quiet: bool = False) -> ReviewServer:
    """Build (but do not run) the server. `port=0` picks a free port; `server.url` tells which."""
    state = ReviewState(session_dir, library_dir)
    server = ReviewServer((host, port), ReviewHandler)
    server.state = state
    server.quiet = quiet
    return server


def serve(session_dir: Path | str, library_dir: Path | str, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the review server until Ctrl-C."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(session_dir, library_dir, host, port)
    state = server.state
    lib_note = str(state.library_dir) if state.library is not None else f"none ({state.library_error})"
    print(f"Biomorphic review UI: {server.url}\n  session: {state.session_dir}\n  library: {lib_note}\n"
          f"  (use --host 0.0.0.0 and this machine's LAN address to open it on a phone)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
