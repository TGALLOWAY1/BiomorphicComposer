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
RENDER_MODES = ("original", "greyscale", "outline", "remap")
TILE_ID_RE = re.compile(r"^(?P<stem>.+)-(?P<session>[0-9a-f]{8})-L(?P<level>\d+)-r(?P<region>\d+)$")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".json": "application/json", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
# Demo palette for `?mode=remap` when the caller gives none: the reel's cool -> warm recolour
# (blues -> brick red, teal -> red-orange, mint -> ochre, grey -> warm grey), in CIE Lab.
DEMO_PALETTE = [
    {"from": [45, 10, -45], "to": [45, 45, 30]},
    {"from": [60, -25, -12], "to": [60, 45, 45]},
    {"from": [85, -18, 12], "to": [78, 8, 50]},
    {"from": [70, 0, 0], "to": [70, 4, 12]},
]


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

    def _tile_json(self, rec: Any) -> dict:
        tid = str(_field(rec, "id"))
        return {
            "id": tid, "png_url": f"/api/tiles/{tid}.png", "side": _field(rec, "side"),
            "pair_tile_id": _field(rec, "pair_tile_id"), "pair_iou": _field(rec, "pair_iou"),
            "area_px": _field(rec, "area_px"), "width": _field(rec, "width"), "height": _field(rec, "height"),
            "created_at": _field(rec, "created_at"),
        }

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
            return {"meta": s.meta, "groups": s.groups, "library": self.library_summary(),
                    "library_dir": str(self.library_dir), "n_leaves": s.tree.n_leaves}

    def regions_json(self, level: Any) -> dict:
        with self.lock:
            i = self._check_level(level)
            regs = self.session.regions(i)
            sides, twin, iou = self._pair_info(i)
            saved = self._saved_index()
            out: dict[str, dict] = {}
            for rid, info in regs.items():
                t = twin.get(rid)
                out[str(rid)] = {
                    "area": int(info.area), "bbox": [int(v) for v in info.bbox],
                    "centroid": [round(float(info.centroid[0]), 2), round(float(info.centroid[1]), 2)],
                    "side": sides.get(rid), "twin": int(t) if t is not None else None,
                    "iou": round(iou[rid], 4) if rid in iou else None,
                    "saved_tile_id": saved.get((i, rid)),
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
            _sides, twin, _iou = self._pair_info(i)
            leaves = sorted({leaf for r in ids for leaf in regs[r].leaves})
            mirror_leaves: list[int] | None = None
            if mirror and all(twin.get(r) is not None for r in ids):
                mirror_leaves = sorted({leaf for r in ids for leaf in regs[twin[r]].leaves})
                if set(mirror_leaves) & set(leaves):      # the twins are among the tapped regions
                    mirror_leaves = None
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
                    partner = saved.get((i, t)) if t is not None else None
                    records[rid] = self._save_one(lib, i, rid, saved, partner, iou.get(rid))
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
            rec = lib.save_tile(self.session, level, rid)
            saved[(level, rid)] = str(_field(rec, "id"))
        tid = str(_field(rec, "id"))
        if partner is not None and partner != tid and _field(rec, "pair_tile_id") != partner:
            lib.link_pair(tid, partner, iou)
            rec = lib.get(tid) or rec
        return rec

    def _save_pair(self, lib: Any, level: int, a: int, b: int, iou: float | None, saved: dict) -> tuple[Any, Any]:
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
            tiles = [self._tile_json(r) for r in self._list_tiles()]
            tiles.sort(key=lambda t: (str(t.get("created_at") or ""), t["id"]))
            return {"tiles": tiles}

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


def boundary_overlay(label_map: np.ndarray, rgba: tuple[int, int, int, int] = INK_RGBA) -> np.ndarray:
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
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
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
        self._send(target.read_bytes(), ctype, cache="no-cache")

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
