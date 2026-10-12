"""SQLite-backed tile library (docs/ARCHITECTURE.md, section Library).

On disk: `<library_dir>/library.sqlite` and `<library_dir>/tiles/<tile_id>.png`.

- Tiles are stored once, as original colour + alpha. Render modes live in render.py and never touch disk.
- A stored tile is never overwritten: saving an id that exists raises TileExists.
- Provenance is written once, together with the tile, and is immutable. There is deliberately no method in this
  module that updates a provenance row, and the `provenance_immutable` trigger aborts any UPDATE at the database
  layer too (`provenance_is_immutable()` proves that on the live database).
- Artist-authored tags live in their own table, separate from provenance.

The only UPDATE statements issued by this API touch the `tiles` table: `link_pair` (and the housekeeping that keeps
pair links symmetric when a partner is re-linked or deleted).
"""
from __future__ import annotations

import json
import numbers
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from biomorphic import tiles as tile_utils
from biomorphic.library import render
from biomorphic.session import GROUP_ID_BASE, Session

__all__ = ["Library", "TileRecord", "LibraryError", "TileExists", "make_tile_id", "SCHEMA_SQL",
           "TILE_COLUMNS", "PROVENANCE_COLUMNS"]

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    sha256 TEXT PRIMARY KEY, filename TEXT, width INT, height INT, first_seen_at TEXT);
CREATE TABLE IF NOT EXISTS tiles (
    id TEXT PRIMARY KEY, created_at TEXT, png_path TEXT, png_sha256 TEXT,
    width INT, height INT, area_px INT, side TEXT,            -- 'L' | 'R' | 'axis'
    pair_tile_id TEXT, pair_iou REAL,
    mean_L REAL, mean_a REAL, mean_b REAL);
CREATE TABLE IF NOT EXISTS provenance (
    tile_id TEXT PRIMARY KEY REFERENCES tiles(id) ON DELETE CASCADE,
    source_sha256 TEXT, source_filename TEXT, session_id TEXT, pipeline_version TEXT,
    axis_x REAL, axis_angle_deg REAL, level_index INT, n_merges INT, region_id INT,
    fragment_ids TEXT,                                        -- JSON list of leaf ids
    bbox_x INT, bbox_y INT, bbox_w INT, bbox_h INT,           -- working px
    crop_x INT, crop_y INT, crop_w INT, crop_h INT,           -- oriented-source px
    working_scale REAL, working_w INT, working_h INT,
    working_angle_deg REAL,                                   -- rotation applied to deskew the working copy
    user_merged INT, created_at TEXT);
CREATE TABLE IF NOT EXISTS tags (
    tile_id TEXT REFERENCES tiles(id) ON DELETE CASCADE, tag TEXT, created_at TEXT,
    PRIMARY KEY (tile_id, tag));
CREATE TRIGGER IF NOT EXISTS provenance_immutable BEFORE UPDATE ON provenance
BEGIN SELECT RAISE(ABORT, 'provenance is immutable'); END;
CREATE INDEX IF NOT EXISTS provenance_by_source ON provenance(source_sha256);
CREATE INDEX IF NOT EXISTS provenance_by_session ON provenance(session_id, level_index, region_id);
"""

TILE_COLUMNS = ("id", "created_at", "png_path", "png_sha256", "width", "height", "area_px", "side",
                "pair_tile_id", "pair_iou", "mean_L", "mean_a", "mean_b")
PROVENANCE_COLUMNS = ("source_sha256", "source_filename", "session_id", "pipeline_version",
                      "axis_x", "axis_angle_deg", "level_index", "n_merges", "region_id", "fragment_ids",
                      "bbox_x", "bbox_y", "bbox_w", "bbox_h", "crop_x", "crop_y", "crop_w", "crop_h",
                      "working_scale", "working_w", "working_h", "working_angle_deg", "user_merged", "created_at")

_SELECT_TILES = ("SELECT " + ", ".join(f"t.{c}" for c in TILE_COLUMNS) + ", "
                 + ", ".join(f"p.{c} AS p_{c}" for c in PROVENANCE_COLUMNS)
                 + " FROM tiles t LEFT JOIN provenance p ON p.tile_id = t.id")

_STEM_UNSAFE = re.compile(r"[^A-Za-z0-9_.+-]")


class LibraryError(Exception):
    """Base class for library errors."""


class TileExists(LibraryError):
    """Raised when saving a tile whose id is already in the library. Stored tiles are never overwritten."""

    def __init__(self, tile_id: str):
        super().__init__(f"tile {tile_id!r} is already in the library (stored tiles are never overwritten)")
        self.tile_id = tile_id


def make_tile_id(source_stem: str, session_id: str, level_index: int, region_id: int) -> str:
    """`<source_stem>-<session_id>-L<level:02d>-r<region_id>`, e.g. IMG_2295-3fa9c1b2-L07-r412.

    Characters that are unsafe in a file name are replaced by `_` in the stem (ordinary stems are unchanged).
    """
    stem = _STEM_UNSAFE.sub("_", str(source_stem)) or "source"
    return f"{stem}-{session_id}-L{int(level_index):02d}-r{int(region_id)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_stem(meta: dict) -> str:
    src = meta.get("source", {})
    name = src.get("filename") or src.get("path") or "source"
    return Path(str(name)).stem or "source"


# ----------------------------------------------------------------------------- records
@dataclass
class TileRecord:
    """One row of `tiles`, plus its provenance row as a dict (fragment_ids decoded to a list of ints)."""

    id: str
    created_at: str
    png_path: str                  # relative to the library dir, e.g. tiles/<id>.png
    png_sha256: str
    width: int
    height: int
    area_px: int
    side: str                      # 'L' | 'R' | 'axis'
    pair_tile_id: str | None
    pair_iou: float | None
    mean_L: float
    mean_a: float
    mean_b: float
    provenance: dict = field(default_factory=dict)

    @property
    def mean_lab(self) -> tuple[float, float, float]:
        return (self.mean_L, self.mean_a, self.mean_b)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _record(row: sqlite3.Row) -> TileRecord:
    vals = tuple(row)
    n = len(TILE_COLUMNS)
    tile = dict(zip(TILE_COLUMNS, vals[:n]))
    prov = dict(zip(PROVENANCE_COLUMNS, vals[n:]))
    if prov.get("session_id") is None and prov.get("source_sha256") is None:
        prov = {}
    elif isinstance(prov.get("fragment_ids"), str):
        prov["fragment_ids"] = [int(i) for i in json.loads(prov["fragment_ids"])]
    return TileRecord(provenance=prov, **tile)


# ----------------------------------------------------------------------------- library
class Library:
    """The tile library in one directory. Use `Library.open(dir)`; safe to share between threads."""

    def __init__(self, library_dir: Path | str, conn: sqlite3.Connection):
        self.dir = Path(library_dir)
        self.db_path = self.dir / "library.sqlite"
        self.tiles_dir = self.dir / "tiles"
        self.conn = conn
        self._lock = threading.RLock()
        self._written_stack: list[list[Path]] = []    # PNGs written per open transaction level (undone on rollback)
        self._unlink_stack: list[list[Path]] = []     # PNGs to remove, applied only when the outermost unit commits

    # ---- lifecycle ----------------------------------------------------------------------
    @classmethod
    def open(cls, library_dir: Path | str) -> "Library":
        """Create `<dir>/tiles/` and `<dir>/library.sqlite` with the schema, or open an existing library (idempotent)."""
        d = Path(library_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "tiles").mkdir(exist_ok=True)
        conn = sqlite3.connect(str(d / "library.sqlite"), isolation_level=None, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL").fetchall()
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA_SQL)
        # libraries created before a column was added: ALTER TABLE is not an UPDATE, so the trigger allows it
        have = {row[1] for row in conn.execute("PRAGMA table_info(provenance)")}
        if "working_angle_deg" not in have:
            conn.execute("ALTER TABLE provenance ADD COLUMN working_angle_deg REAL")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version > SCHEMA_VERSION:
            conn.close()
            raise LibraryError(f"library schema version {version} is newer than this code ({SCHEMA_VERSION})")
        return cls(d, conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Library":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Library({str(self.dir)!r})"

    # ---- transactions -------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several calls into one atomic unit; nested use becomes a savepoint.

        PNG files written inside a unit that rolls back are removed again, so disk and database stay in step.
        """
        with self._lock:
            depth = len(self._written_stack)
            if depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")
            else:
                self.conn.execute(f"SAVEPOINT lib_sp_{depth}")
            self._written_stack.append([])
            self._unlink_stack.append([])
            try:
                yield
            except BaseException:
                written = self._written_stack.pop()
                self._unlink_stack.pop()          # the rows come back, so their files must stay
                if depth == 0:
                    self.conn.execute("ROLLBACK")
                else:
                    self.conn.execute(f"ROLLBACK TO lib_sp_{depth}")
                    self.conn.execute(f"RELEASE lib_sp_{depth}")
                for p in written:
                    p.unlink(missing_ok=True)
                raise
            else:
                written = self._written_stack.pop()
                pending = self._unlink_stack.pop()
                if depth == 0:
                    self.conn.execute("COMMIT")
                    for p in pending:
                        p.unlink(missing_ok=True)
                else:
                    self.conn.execute(f"RELEASE lib_sp_{depth}")
                    self._written_stack[-1].extend(written)
                    self._unlink_stack[-1].extend(pending)

    # ---- ids ----------------------------------------------------------------------------
    @staticmethod
    def tile_id_for(session: Session, level_index: int, region_id: int) -> str:
        """The id a tile of this region would get (deterministic, so callers can look up saved tiles)."""
        return make_tile_id(_source_stem(session.meta), session.session_id, level_index, region_id)

    def _exists(self, tile_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM tiles WHERE id = ?", (tile_id,)).fetchone() is not None

    # ---- writes -------------------------------------------------------------------------
    def save_tile(self, session: Session, level_index: int, region_id: int, *,
                  pair_with: str | int | TileRecord | None = None, pair_iou: float | None = None) -> TileRecord:
        """Cut one region, write `<dir>/tiles/<tile_id>.png` and the sources/tiles/provenance rows, in one transaction.

        `pair_with` links the new tile with an existing one: a tile id (str), or a region id (int) at the same
        level of the same session. `pair_iou` defaults to the session's pairing IoU for that pair when the partner
        comes from the same session and level, else NULL. Raises TileExists if the tile id is already stored.
        """
        level_index, region_id = int(level_index), int(region_id)
        with self.transaction():
            rec = self._insert_tile(session, level_index, region_id)
            if pair_with is not None:
                partner_id = self._resolve_pair_with(session, level_index, pair_with)
                if pair_iou is None:
                    partner = self.get(partner_id)
                    prov = partner.provenance if partner else {}
                    if prov.get("session_id") == session.session_id and prov.get("level_index") == level_index:
                        pair_iou = _pair_iou_from_session(session, level_index, region_id, int(prov["region_id"]))
                self.link_pair(rec.id, partner_id, pair_iou)
            return self.get(rec.id)

    def save_pair(self, session: Session, level_index: int, region_a: int, region_b: int, *,
                  iou: float | None = None) -> tuple[TileRecord, TileRecord]:
        """Save two regions and link them as a mirror pair, all in one transaction.

        `iou` defaults to the IoU the session's pairing recorded for the two regions (NULL if they are not twins
        there, e.g. a pair the artist chose by hand).
        """
        level_index, region_a, region_b = int(level_index), int(region_a), int(region_b)
        if region_a == region_b:
            raise ValueError("a pair needs two different regions")
        with self.transaction():
            a = self._insert_tile(session, level_index, region_a)
            b = self._insert_tile(session, level_index, region_b)
            if iou is None:
                iou = _pair_iou_from_session(session, level_index, region_a, region_b)
            self.link_pair(a.id, b.id, iou)
            return self.get(a.id), self.get(b.id)

    def link_pair(self, a_id: str, b_id: str, iou: float | None) -> None:
        """Set pair_tile_id / pair_iou on both tiles (symmetric). Any stale link of a previous partner is cleared.

        This is the only kind of UPDATE the API issues, and it only ever touches `tiles`.
        """
        a_id, b_id = str(a_id), str(b_id)
        if a_id == b_id:
            raise ValueError("a tile cannot be paired with itself")
        iou_val = None if iou is None else float(iou)
        with self.transaction():
            for tid in (a_id, b_id):
                if not self._exists(tid):
                    raise KeyError(f"no tile {tid!r} in the library")
            self.conn.execute("UPDATE tiles SET pair_tile_id = NULL, pair_iou = NULL "
                              "WHERE pair_tile_id IN (?, ?) AND id NOT IN (?, ?)", (a_id, b_id, a_id, b_id))
            self.conn.execute("UPDATE tiles SET pair_tile_id = ?, pair_iou = ? WHERE id = ?", (b_id, iou_val, a_id))
            self.conn.execute("UPDATE tiles SET pair_tile_id = ?, pair_iou = ? WHERE id = ?", (a_id, iou_val, b_id))

    def add_tag(self, tile_id: str, tag: str) -> None:
        """Attach an artist-authored tag (idempotent). Tags live in their own table, never in provenance."""
        tile_id, tag = str(tile_id), str(tag).strip()
        if not tag:
            raise ValueError("tag must not be empty")
        with self.transaction():
            if not self._exists(tile_id):
                raise KeyError(f"no tile {tile_id!r} in the library")
            self.conn.execute("INSERT OR IGNORE INTO tags (tile_id, tag, created_at) VALUES (?, ?, ?)",
                              (tile_id, tag, _now_iso()))

    def remove_tag(self, tile_id: str, tag: str) -> None:
        tile_id, tag = str(tile_id), str(tag).strip()
        with self.transaction():
            if not self._exists(tile_id):
                raise KeyError(f"no tile {tile_id!r} in the library")
            self.conn.execute("DELETE FROM tags WHERE tile_id = ? AND tag = ?", (tile_id, tag))

    def delete_tile(self, tile_id: str) -> None:
        """Remove a tile: its rows (provenance and tags cascade), its partner's link to it, and its PNG."""
        tile_id = str(tile_id)
        with self.transaction():
            row = self.conn.execute("SELECT png_path FROM tiles WHERE id = ?", (tile_id,)).fetchone()
            if row is None:
                raise KeyError(f"no tile {tile_id!r} in the library")
            png_path = self.dir / row[0] if row[0] else None
            self.conn.execute("UPDATE tiles SET pair_tile_id = NULL, pair_iou = NULL WHERE pair_tile_id = ?", (tile_id,))
            self.conn.execute("DELETE FROM tiles WHERE id = ?", (tile_id,))
            if png_path is not None:
                self._unlink_stack[-1].append(png_path)   # removed when the outermost unit commits, never on rollback

    # ---- reads --------------------------------------------------------------------------
    def get(self, tile_id: str) -> TileRecord | None:
        with self._lock:
            row = self.conn.execute(_SELECT_TILES + " WHERE t.id = ?", (str(tile_id),)).fetchone()
        return _record(row) if row is not None else None

    def list_tiles(self, source_sha256: str | None = None) -> list[TileRecord]:
        """Every tile in save order, optionally only those cut from one source image."""
        sql, args = _SELECT_TILES, ()
        if source_sha256 is not None:
            sql += " WHERE p.source_sha256 = ?"
            args = (str(source_sha256),)
        sql += " ORDER BY t.created_at, t.rowid"
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [_record(r) for r in rows]

    def tile_path(self, tile_id: str) -> Path:
        """Absolute path of the stored PNG. Raises KeyError for an unknown tile."""
        with self._lock:
            row = self.conn.execute("SELECT png_path FROM tiles WHERE id = ?", (str(tile_id),)).fetchone()
        if row is None:
            raise KeyError(f"no tile {tile_id!r} in the library")
        return self.dir / row[0]

    def tile_png_bytes(self, tile_id: str) -> bytes:
        """The stored PNG, byte for byte (the only asset ever written for a tile)."""
        return self.tile_path(tile_id).read_bytes()

    def tags(self, tile_id: str) -> list[str]:
        with self._lock:
            rows = self.conn.execute("SELECT tag FROM tags WHERE tile_id = ? ORDER BY tag", (str(tile_id),)).fetchall()
        return [r[0] for r in rows]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            n_tiles = int(self.conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0])
            n_sources = int(self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
        return {"n_tiles": n_tiles, "n_sources": n_sources, "dir": str(self.dir)}

    def provenance_is_immutable(self) -> bool:
        """True iff the database refuses an UPDATE on a provenance row (verify check 8).

        The probe runs inside a savepoint that is always rolled back, so it changes nothing even if the trigger
        were missing. On an empty library a throw-away tile + provenance row is inserted (and rolled back) so
        that the trigger has a row to fire on.
        """
        with self._lock:
            self.conn.execute("SAVEPOINT provenance_probe")
            try:
                row = self.conn.execute("SELECT tile_id FROM provenance LIMIT 1").fetchone()
                if row is None:
                    self.conn.execute("INSERT INTO tiles (id) VALUES ('__provenance_probe__')")
                    self.conn.execute("INSERT INTO provenance (tile_id, axis_x) VALUES ('__provenance_probe__', 0.0)")
                    tile_id = "__provenance_probe__"
                else:
                    tile_id = row[0]
                try:
                    # The one place this module writes an UPDATE against provenance: it must fail.
                    self.conn.execute("UPDATE provenance SET axis_x = COALESCE(axis_x, 0) + 1 WHERE tile_id = ?",
                                      (tile_id,))
                except sqlite3.IntegrityError:
                    return True
                return False
            finally:
                self.conn.execute("ROLLBACK TO provenance_probe")
                self.conn.execute("RELEASE provenance_probe")

    # ---- internals ----------------------------------------------------------------------
    def _resolve_pair_with(self, session: Session, level_index: int, pair_with: str | int | TileRecord) -> str:
        if isinstance(pair_with, TileRecord):
            partner_id = pair_with.id
        elif isinstance(pair_with, numbers.Integral):
            partner_id = self.tile_id_for(session, level_index, int(pair_with))
        else:
            partner_id = str(pair_with)
        if not self._exists(partner_id):
            raise KeyError(f"pair_with: no tile {partner_id!r} in the library")
        return partner_id

    def _insert_tile(self, session: Session, level_index: int, region_id: int) -> TileRecord:
        """Insert sources (if new), tiles and provenance rows and write the PNG. Must run inside `transaction()`."""
        assert self._written_stack, "_insert_tile must run inside a transaction"
        meta = session.meta
        tile_id = self.tile_id_for(session, level_index, region_id)
        if self._exists(tile_id):
            raise TileExists(tile_id)

        rgba, bbox = session.cut_tile(level_index, region_id)
        info = session.regions(level_index)[region_id]
        sides = session.pairing(level_index).get("sides", {})
        side = sides.get(region_id) or ("L" if info.centroid[0] < session.axis_x else "R")
        png = tile_utils.encode_png(rgba)
        png_sha = tile_utils.sha256(png)
        mean_L, mean_a, mean_b = render.mean_lab(rgba)
        now = _now_iso()

        src = meta.get("source", {})
        crop = meta.get("crop", {})
        working = meta.get("working", {})
        axis = meta.get("axis", {})
        rel_path = f"tiles/{tile_id}.png"

        self.conn.execute(
            "INSERT OR IGNORE INTO sources (sha256, filename, width, height, first_seen_at) VALUES (?, ?, ?, ?, ?)",
            (src.get("sha256"), src.get("filename"), src.get("width"), src.get("height"), now))
        self.conn.execute(
            "INSERT INTO tiles (id, created_at, png_path, png_sha256, width, height, area_px, side, "
            "pair_tile_id, pair_iou, mean_L, mean_a, mean_b) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (tile_id, now, rel_path, png_sha, int(rgba.shape[1]), int(rgba.shape[0]),
             int((rgba[..., 3] > 0).sum()), side, float(mean_L), float(mean_a), float(mean_b)))
        self.conn.execute(
            "INSERT INTO provenance (tile_id, " + ", ".join(PROVENANCE_COLUMNS) + ") VALUES ("
            + ", ".join("?" * (len(PROVENANCE_COLUMNS) + 1)) + ")",
            (tile_id,
             src.get("sha256"), src.get("filename"), session.session_id, meta.get("pipeline_version"),
             float(axis.get("x", session.axis_x)), float(axis.get("angle_deg", 0.0)),
             level_index, session.level_n_merges(level_index), region_id,
             json.dumps([int(i) for i in info.leaves]),
             int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]),
             crop.get("x"), crop.get("y"), crop.get("w"), crop.get("h"),
             working.get("scale"), working.get("width"), working.get("height"),
             float(working.get("angle_deg", 0.0) or 0.0),
             1 if region_id >= GROUP_ID_BASE else 0, now))

        path = self.dir / rel_path
        path.write_bytes(png)                    # an orphan PNG without a row is not a stored tile; it may be replaced
        self._written_stack[-1].append(path)
        return self.get(tile_id)


def _pair_iou_from_session(session: Session, level_index: int, region_a: int, region_b: int) -> float | None:
    """The pairing IoU recorded by the session for these two regions, or None if they are not twins there."""
    want = {int(region_a), int(region_b)}
    for pr in session.pairing(level_index).get("pairs", []):
        if {int(pr["a"]), int(pr["b"])} == want:
            return float(pr["iou"])
    return None
