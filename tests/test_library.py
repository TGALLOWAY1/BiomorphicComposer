import json
import sqlite3
import threading

import numpy as np
import pytest

from biomorphic.library import render
from biomorphic.library.db import Library, TileExists, TileRecord, make_tile_id
from biomorphic.session import GROUP_ID_BASE, Session
from biomorphic.tiles import decode_png, sha256

SOURCE_SHA = "0" * 64


@pytest.fixture
def session(synthetic_dir) -> Session:
    return Session.load(synthetic_dir)


@pytest.fixture
def lib_dir(tmp_path):
    return tmp_path / "lib"


@pytest.fixture
def lib(lib_dir):
    library = Library.open(lib_dir)
    yield library
    library.close()


def _schema_names(lib: Library) -> set:
    rows = lib.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')").fetchall()
    return {r[0] for r in rows}


# ----------------------------------------------------------------------------- open
def test_open_creates_schema_and_is_idempotent(lib_dir):
    lib = Library.open(lib_dir)
    assert (lib_dir / "library.sqlite").is_file() and (lib_dir / "tiles").is_dir()
    assert {"sources", "tiles", "provenance", "tags", "provenance_immutable"} <= _schema_names(lib)
    assert lib.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert lib.summary() == {"n_tiles": 0, "n_sources": 0, "dir": str(lib_dir)}
    lib.close()
    again = Library.open(lib_dir)          # opening an existing library changes nothing
    assert {"sources", "tiles", "provenance", "tags", "provenance_immutable"} <= _schema_names(again)
    assert again.summary()["n_tiles"] == 0
    again.close()


def test_make_tile_id_format(session):
    assert make_tile_id("IMG_2295", "3fa9c1b2", 7, 412) == "IMG_2295-3fa9c1b2-L07-r412"
    assert make_tile_id("IMG_2295", "3fa9c1b2", 3, GROUP_ID_BASE) == f"IMG_2295-3fa9c1b2-L03-r{GROUP_ID_BASE}"
    assert make_tile_id("odd name/x", "abcd1234", 0, 1) == "odd_name_x-abcd1234-L00-r1"
    assert Library.tile_id_for(session, 0, 1) == f"synthetic-{session.session_id}-L00-r1"


# ----------------------------------------------------------------------------- save_tile
def test_save_tile_writes_png_and_rows(lib, lib_dir, session):
    rec = lib.save_tile(session, 0, 1)
    assert isinstance(rec, TileRecord)
    assert rec.id == f"synthetic-{session.session_id}-L00-r1"

    rgba, bbox = session.cut_tile(0, 1)
    path = lib.tile_path(rec.id)
    assert path == lib_dir / "tiles" / f"{rec.id}.png" and path.is_file()
    assert rec.png_path == f"tiles/{rec.id}.png"          # relative: the library directory can move
    data = path.read_bytes()
    assert np.array_equal(decode_png(data), rgba)        # stored exactly as cut: original colour + alpha
    assert lib.tile_png_bytes(rec.id) == data
    assert rec.png_sha256 == sha256(data)
    assert (rec.width, rec.height) == (bbox[2], bbox[3]) == (50, 60)
    assert rec.area_px == int((rgba[..., 3] > 0).sum()) == 50 * 60
    assert rec.side == "L"
    assert rec.pair_tile_id is None and rec.pair_iou is None
    assert np.allclose(rec.mean_lab, session.regions(0)[1].mean_lab, atol=0.01)
    assert np.allclose(rec.mean_lab, render.mean_lab(rgba))
    assert rec.created_at.endswith("Z") and "T" in rec.created_at

    p = rec.provenance
    assert p["source_sha256"] == SOURCE_SHA and p["source_filename"] == "synthetic.png"
    assert p["session_id"] == session.session_id
    assert p["pipeline_version"] == session.meta["pipeline_version"]
    assert p["axis_x"] == 120.0 and p["axis_angle_deg"] == 0.0
    assert (p["level_index"], p["n_merges"], p["region_id"]) == (0, 0, 1)
    assert p["fragment_ids"] == [1]
    assert (p["bbox_x"], p["bbox_y"], p["bbox_w"], p["bbox_h"]) == bbox == (20, 30, 50, 60)
    assert (p["crop_x"], p["crop_y"], p["crop_w"], p["crop_h"]) == (0, 0, 240, 200)
    assert (p["working_scale"], p["working_w"], p["working_h"]) == (1.0, 240, 200)
    assert p["user_merged"] == 0
    assert p["created_at"] == rec.created_at
    raw = lib.conn.execute("SELECT fragment_ids FROM provenance WHERE tile_id = ?", (rec.id,)).fetchone()[0]
    assert json.loads(raw) == [1]                        # stored as JSON text

    src = lib.conn.execute("SELECT sha256, filename, width, height FROM sources").fetchall()
    assert [tuple(r) for r in src] == [(SOURCE_SHA, "synthetic.png", 240, 200)]
    assert lib.summary() == {"n_tiles": 1, "n_sources": 1, "dir": str(lib_dir)}
    assert rec.to_json()["provenance"]["region_id"] == 1


def test_save_tile_at_coarser_level_records_n_merges(lib, session):
    rec = lib.save_tile(session, 1, 6)                   # cells 1 + 2 merged
    assert rec.id.endswith("-L01-r6")
    assert rec.provenance["n_merges"] == 2 and rec.provenance["level_index"] == 1
    assert rec.provenance["fragment_ids"] == [1, 2]
    rgba, bbox = session.cut_tile(1, 6)
    assert (rec.width, rec.height) == (bbox[2], bbox[3])
    assert rec.area_px == int((rgba[..., 3] > 0).sum()) < rec.width * rec.height   # paper gap between the cells


def test_save_group_region_is_user_merged(lib, session):
    gid = GROUP_ID_BASE + session.add_group([1, 5])
    rec = lib.save_tile(session, 0, gid)
    assert rec.id.endswith(f"-L00-r{gid}")
    assert rec.provenance["user_merged"] == 1
    assert rec.provenance["fragment_ids"] == [1, 5] and rec.provenance["region_id"] == gid
    assert rec.side in ("L", "R", "axis")


def test_save_tile_raises_tile_exists_and_never_overwrites(lib, session):
    rec = lib.save_tile(session, 0, 1)
    before = lib.tile_png_bytes(rec.id)
    with pytest.raises(TileExists) as ei:
        lib.save_tile(session, 0, 1)
    assert ei.value.tile_id == rec.id
    assert lib.tile_png_bytes(rec.id) == before
    assert lib.conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 1
    assert lib.get(rec.id) == rec


def test_save_tile_unknown_region_writes_nothing(lib, lib_dir, session):
    with pytest.raises(KeyError):
        lib.save_tile(session, 0, 999)
    assert lib.summary()["n_tiles"] == 0
    assert list((lib_dir / "tiles").iterdir()) == []


# ----------------------------------------------------------------------------- pairs
def test_save_pair_links_both(lib, session):
    a, b = lib.save_pair(session, 0, 1, 3)
    assert (a.side, b.side) == ("L", "R")
    assert a.pair_tile_id == b.id and b.pair_tile_id == a.id
    assert a.pair_iou == b.pair_iou and a.pair_iou > 0.99      # perfect mirror in the synthetic drawing
    assert lib.get(a.id).pair_tile_id == b.id and lib.get(b.id).pair_tile_id == a.id
    with pytest.raises(ValueError):
        lib.save_pair(session, 0, 2, 2)
    c, d = lib.save_pair(session, 0, 2, 4, iou=0.77)     # the caller's IoU wins when given
    assert c.pair_iou == d.pair_iou == 0.77 and c.pair_tile_id == d.id


def test_save_pair_is_one_transaction(lib, lib_dir, session):
    t3 = lib.save_tile(session, 0, 3)
    with pytest.raises(TileExists):
        lib.save_pair(session, 0, 1, 3)                  # tile 1 is written first, then 3 collides
    id1 = Library.tile_id_for(session, 0, 1)
    assert lib.get(id1) is None                          # rolled back ...
    assert not (lib_dir / "tiles" / f"{id1}.png").exists()   # ... including the PNG written before the failure
    assert lib.get(t3.id).pair_tile_id is None
    assert lib.summary()["n_tiles"] == 1


def test_save_tile_pair_with_existing_tile(lib, session):
    t2 = lib.save_tile(session, 0, 2)
    t4 = lib.save_tile(session, 0, 4, pair_with=t2.id)   # iou looked up from the session pairing
    assert t4.pair_tile_id == t2.id and lib.get(t2.id).pair_tile_id == t4.id
    assert t4.pair_iou > 0.99 and lib.get(t2.id).pair_iou == t4.pair_iou
    t6 = lib.save_tile(session, 1, 6)
    t7 = lib.save_tile(session, 1, 7, pair_with=6, pair_iou=0.42)   # region-id form, explicit iou
    assert t7.pair_tile_id == t6.id and lib.get(t6.id).pair_iou == 0.42 == t7.pair_iou
    with pytest.raises(KeyError):
        lib.save_tile(session, 0, 1, pair_with="not-a-tile")
    assert lib.get(Library.tile_id_for(session, 0, 1)) is None      # rolled back with the failed link


def test_link_pair_is_symmetric_and_clears_stale_links(lib, session):
    a = lib.save_tile(session, 0, 1)
    b = lib.save_tile(session, 0, 3)
    c = lib.save_tile(session, 0, 2)
    lib.link_pair(a.id, b.id, 0.9)
    assert lib.get(a.id).pair_tile_id == b.id and lib.get(b.id).pair_tile_id == a.id
    assert lib.get(a.id).pair_iou == 0.9
    lib.link_pair(a.id, c.id, 0.3)
    assert lib.get(a.id).pair_tile_id == c.id and lib.get(c.id).pair_tile_id == a.id
    assert lib.get(b.id).pair_tile_id is None and lib.get(b.id).pair_iou is None
    lib.link_pair(a.id, c.id, None)
    assert lib.get(a.id).pair_tile_id == c.id and lib.get(a.id).pair_iou is None
    with pytest.raises(ValueError):
        lib.link_pair(a.id, a.id, 1.0)
    with pytest.raises(KeyError):
        lib.link_pair(a.id, "nope", 1.0)


# ----------------------------------------------------------------------------- tags
def test_tags_add_remove(lib, session):
    rec = lib.save_tile(session, 0, 1)
    assert lib.tags(rec.id) == []
    lib.add_tag(rec.id, "head")
    lib.add_tag(rec.id, " left ")
    lib.add_tag(rec.id, "head")                          # idempotent
    assert lib.tags(rec.id) == ["head", "left"]
    lib.remove_tag(rec.id, "head")
    assert lib.tags(rec.id) == ["left"]
    lib.remove_tag(rec.id, "absent")                     # no error
    with pytest.raises(KeyError):
        lib.add_tag("nope", "x")
    with pytest.raises(KeyError):
        lib.remove_tag("nope", "x")
    with pytest.raises(ValueError):
        lib.add_tag(rec.id, "   ")
    # tags are their own namespace: nothing about them lands in provenance
    assert lib.get(rec.id).provenance == rec.provenance
    assert "tag" not in {c.lower() for c in rec.provenance}


# ----------------------------------------------------------------------------- delete / list / get
def test_delete_tile_removes_rows_file_and_partner_link(lib, session):
    a, b = lib.save_pair(session, 0, 1, 3)
    lib.add_tag(a.id, "head")
    path = lib.tile_path(a.id)
    lib.delete_tile(a.id)
    assert lib.get(a.id) is None and not path.exists()
    assert lib.conn.execute("SELECT COUNT(*) FROM provenance WHERE tile_id = ?", (a.id,)).fetchone()[0] == 0
    assert lib.conn.execute("SELECT COUNT(*) FROM tags WHERE tile_id = ?", (a.id,)).fetchone()[0] == 0
    partner = lib.get(b.id)
    assert partner is not None and partner.pair_tile_id is None and partner.pair_iou is None
    assert lib.tile_path(b.id).exists()
    with pytest.raises(KeyError):
        lib.delete_tile(a.id)
    again = lib.save_tile(session, 0, 1)                 # the id is free again
    assert again.id == a.id and again.pair_tile_id is None


def test_list_and_get(lib, lib_dir, session):
    recs = [lib.save_tile(session, 0, r) for r in (1, 2, 5)]
    ids = [r.id for r in recs]
    assert [r.id for r in lib.list_tiles()] == ids       # save order
    assert [r.id for r in lib.list_tiles(source_sha256=SOURCE_SHA)] == ids
    assert lib.list_tiles(source_sha256="f" * 64) == []
    assert lib.list_tiles()[2].side == "axis" and lib.list_tiles()[2].provenance["region_id"] == 5
    got = lib.get(ids[1])
    assert isinstance(got, TileRecord) and got == recs[1]
    assert lib.get("nope") is None
    with pytest.raises(KeyError):
        lib.tile_png_bytes("nope")
    with pytest.raises(KeyError):
        lib.tile_path("nope")
    lib.close()
    reopened = Library.open(lib_dir)                     # everything persisted
    assert [r.id for r in reopened.list_tiles()] == ids
    assert reopened.get(ids[0]).provenance["fragment_ids"] == [1]
    reopened.close()


def test_library_is_usable_from_another_thread(lib, session):
    rec = lib.save_tile(session, 0, 1)
    seen = []
    t = threading.Thread(target=lambda: seen.append(lib.get(rec.id).id))
    t.start()
    t.join()
    assert seen == [rec.id]


# ----------------------------------------------------------------------------- non-negotiables
def test_provenance_is_immutable(lib, session):
    assert lib.provenance_is_immutable()                 # on an empty library (probe row rolled back) ...
    assert lib.summary()["n_tiles"] == 0
    rec = lib.save_tile(session, 0, 1)
    assert lib.provenance_is_immutable()                 # ... and with real rows
    with pytest.raises(sqlite3.IntegrityError):          # RAISE(ABORT) surfaces as IntegrityError
        lib.conn.execute("UPDATE provenance SET axis_x = 999 WHERE tile_id = ?", (rec.id,))
    with pytest.raises(sqlite3.IntegrityError):
        lib.conn.execute("UPDATE provenance SET fragment_ids = '[9]'")
    assert lib.get(rec.id).provenance == rec.provenance
    lib.add_tag(rec.id, "still-works")                   # the connection is healthy afterwards
    assert lib.tags(rec.id) == ["still-works"]
    # the trigger lives in the database file: a second, independent connection is refused as well
    other = sqlite3.connect(str(lib.db_path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            other.execute("UPDATE provenance SET session_id = 'tampered'")
        other.rollback()
    finally:
        other.close()
    assert lib.get(rec.id).provenance["session_id"] == session.session_id
    # no API path: nothing public on Library updates anything but pair links, and nothing names provenance
    public = [n for n in dir(Library) if not n.startswith("_")]
    assert not [n for n in public if "update" in n.lower() or "set_" in n.lower()]
    assert [n for n in public if "provenance" in n.lower()] == ["provenance_is_immutable"]


def test_render_modes_never_touch_the_stored_png(lib, lib_dir, session):
    rec = lib.save_tile(session, 0, 1)
    before = sha256(lib.tile_png_bytes(rec.id))
    assert before == rec.png_sha256
    rgba = decode_png(lib.tile_png_bytes(rec.id))
    palette = [{"from": list(rec.mean_lab), "to": [rec.mean_L, rec.mean_a + 30, rec.mean_b + 30]}]
    for mode in render.MODES:
        out = render.render(rgba, mode, palette=palette)
        assert out.shape == rgba.shape
        assert sha256(lib.tile_png_bytes(rec.id)) == before
    assert {p.name for p in (lib_dir / "tiles").iterdir()} == {f"{rec.id}.png"}   # no derived assets on disk
    assert lib.get(rec.id) == rec


def test_delete_inside_a_rolled_back_transaction_keeps_the_png(synthetic_dir, tmp_path):
    from biomorphic.library.db import Library
    from biomorphic.session import Session
    s = Session.load(synthetic_dir)
    lib = Library.open(tmp_path / "lib")
    rec = lib.save_tile(s, 0, 2)
    path = lib.tile_path(rec.id)
    assert path.exists()
    with pytest.raises(RuntimeError):
        with lib.transaction():
            lib.delete_tile(rec.id)
            raise RuntimeError("abort")
    assert lib.get(rec.id) is not None and path.exists()
    assert lib.tile_png_bytes(rec.id)
    lib.delete_tile(rec.id)
    assert lib.get(rec.id) is None and not path.exists()


def test_provenance_records_the_deskew_angle(synthetic_dir, tmp_path):
    import json
    from biomorphic.library.db import Library
    from biomorphic.session import Session
    meta_path = synthetic_dir / "session.json"
    meta = json.loads(meta_path.read_text())
    meta["working"]["angle_deg"] = 1.5
    meta["axis"]["angle_deg"] = 0.0
    meta_path.write_text(json.dumps(meta))
    s = Session.load(synthetic_dir)
    lib = Library.open(tmp_path / "lib")
    rec = lib.save_tile(s, 0, 1)
    assert rec.provenance["working_angle_deg"] == pytest.approx(1.5)
    assert rec.provenance["axis_angle_deg"] == pytest.approx(0.0)
    # a library created without the column is upgraded on open
    import sqlite3
    old = tmp_path / "old"
    old.mkdir()
    from biomorphic.library.db import PROVENANCE_COLUMNS
    conn = sqlite3.connect(old / "library.sqlite")
    cols = ", ".join(f"{c} TEXT" for c in PROVENANCE_COLUMNS if c != "working_angle_deg")
    conn.executescript(f"CREATE TABLE provenance (tile_id TEXT PRIMARY KEY, {cols});")
    conn.commit()
    conn.close()
    lib2 = Library.open(old)
    cols = {r[1] for r in lib2.conn.execute("PRAGMA table_info(provenance)")}
    assert "working_angle_deg" in cols


# ----------------------------------------------------------------------------- composites
def test_save_composite_stores_the_components_too(lib, lib_dir, session):
    gid = GROUP_ID_BASE + session.add_group([1, 2])
    rec = lib.save_composite(session, 1, gid)
    assert rec.is_composite and rec.provenance["user_merged"] == 1
    assert rec.id.endswith(f"-L01-r{gid}") and rec.provenance["fragment_ids"] == [1, 2]
    # the components are level-0 tiles, cut as the level's own regions although the group covers them
    assert rec.members == [make_tile_id("synthetic", session.session_id, 0, 1),
                           make_tile_id("synthetic", session.session_id, 0, 2)]
    members = lib.members(rec.id)
    assert [m.id for m in members] == rec.members
    assert members[0].area_px == 50 * 60 and members[1].area_px == 50 * 70 and not members[0].is_composite
    assert members[0].provenance["fragment_ids"] == [1] and members[0].provenance["user_merged"] == 0
    assert (lib_dir / "tiles" / f"{members[0].id}.png").exists()
    assert lib.composites_of(members[0].id) == [rec.id]
    assert {t.id for t in lib.list_tiles()} == {rec.id, *rec.members} and lib.summary()["n_tiles"] == 3
    assert [t.members for t in lib.list_tiles() if t.id == rec.id] == [rec.members]   # list_tiles carries members too
    # a component saved earlier is reused, not duplicated
    gid2 = GROUP_ID_BASE + session.add_group([1, 2, 5])        # absorbs the first group (same index 0)
    assert gid2 == gid
    lib.delete_tile(rec.id)
    rec2 = lib.save_composite(session, 0, gid2)
    assert rec2.members[:2] == rec.members and len(rec2.members) == 3 and lib.summary()["n_tiles"] == 4


def test_composite_is_one_transaction_and_members_survive_its_deletion(lib, lib_dir, session):
    gid = GROUP_ID_BASE + session.add_group([3, 4])
    rec = lib.save_composite(session, 0, gid)
    lib.delete_tile(rec.id)
    assert lib.get(rec.id) is None and not (lib_dir / "tiles" / f"{rec.id}.png").exists()
    assert [m.id for m in lib.list_tiles()] == rec.members                 # components stay
    assert lib.composites_of(rec.members[0]) == []
    assert lib.conn.execute("SELECT COUNT(*) FROM tile_members").fetchone()[0] == 0
    # saving the same composite twice raises and leaves nothing behind
    rec = lib.save_composite(session, 0, gid)
    with pytest.raises(TileExists):
        lib.save_composite(session, 0, gid)
    assert lib.summary()["n_tiles"] == 3
    with pytest.raises(ValueError):
        lib.save_composite(session, 0, 3)                                   # not a group
