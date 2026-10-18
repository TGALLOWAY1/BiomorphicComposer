"""The review server's routes, exercised over real HTTP on the synthetic session (docs/ARCHITECTURE.md, Server + UI)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from biomorphic.server.app import BOUNDARY_RGBA, boundary_overlay, make_server
from biomorphic.session import GROUP_ID_BASE, Session
from biomorphic.testing import RECTS
from biomorphic.tiles import decode_png


@pytest.fixture
def server(synthetic_dir: Path, tmp_path: Path):
    srv = make_server(synthetic_dir, tmp_path / "library", "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def get(srv, path: str):
    """(status, content_type, body bytes); never raises on an HTTP error status."""
    try:
        with urllib.request.urlopen(srv.url.rstrip("/") + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def get_json(srv, path: str, expect: int = 200):
    status, ctype, body = get(srv, path)
    assert status == expect, (status, body[:200])
    assert ctype.startswith("application/json")
    return json.loads(body)


def post_json(srv, path: str, obj, expect: int = 200, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(obj).encode()
    req = urllib.request.Request(srv.url.rstrip("/") + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    assert status == expect, (status, body[:300])
    return json.loads(body)


def decode_labels(png: bytes) -> np.ndarray:
    rgba = decode_png(png).astype(np.int32)
    return rgba[..., 0] | (rgba[..., 1] << 8) | (rgba[..., 2] << 16)


# ----------------------------------------------------------------------------- static + session
def test_index_and_static_files(server):
    status, ctype, body = get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    for hook in ('id="stage"', 'id="slider"', 'id="count"', 'id="btn-merge"', 'id="btn-save"', 'id="btn-clear"',
                 'id="mirror-merges"', 'id="tray"', 'id="toast"'):
        assert hook in body.decode()
    assert "cdn." not in body.decode() and "http://" not in body.decode().replace("http://www.w3.org", "")
    assert get(server, "/static/index.html")[0] == 200
    assert get(server, "/static/../app.py")[0] == 404
    assert get(server, "/static/nope.js")[0] == 404
    status, ctype, body = get(server, "/nowhere")
    assert status == 404 and ctype.startswith("application/json") and "error" in json.loads(body)
    assert get(server, "/api/merge")[0] == 405           # GET on a POST route


def test_session_json(server, synthetic_dir):
    j = get_json(server, "/api/session")
    meta = json.loads((synthetic_dir / "session.json").read_text())
    assert j["meta"] == meta
    assert j["groups"] == []
    assert "library" in j and (j["library"] is None or j["library"]["tile_count"] == 0)
    assert j["library_dir"].endswith("library")
    assert j["n_leaves"] == 5


# ----------------------------------------------------------------------------- images
def test_working_png(server):
    status, ctype, body = get(server, "/api/working.png")
    assert status == 200 and ctype == "image/png"
    rgba = decode_png(body)
    assert rgba.shape == (200, 240, 4)
    assert tuple(rgba[60, 45, :3]) == RECTS[1][4]            # inside cell 1, the cell's colour


def test_labels_png_decodes_to_the_exact_label_map(server, synthetic_dir):
    s = Session.load(synthetic_dir)
    for i in range(len(s.levels)):
        status, ctype, body = get(server, f"/api/levels/{i}/labels.png")
        assert status == 200 and ctype == "image/png"
        assert np.array_equal(decode_labels(body), s.label_map(i)), f"level {i}"
    assert get(server, "/api/levels/9/labels.png")[0] == 404
    assert get(server, "/api/levels/-1/labels.png")[0] == 404


def test_overlay_png_is_rgba_with_2px_boundaries(server):
    status, ctype, body = get(server, "/api/levels/0/overlay.png")
    assert status == 200 and ctype == "image/png"
    rgba = decode_png(body)
    assert rgba.shape == (200, 240, 4)
    alpha = rgba[..., 3]
    assert set(np.unique(alpha).tolist()) == {0, 255}
    # cell 1 spans x 20..69: the edge pixels on both sides of x = 20 are boundary, the interior is not
    assert alpha[60, 19] == 255 and alpha[60, 20] == 255 and alpha[60, 18] == 0 and alpha[60, 21] == 0
    assert alpha[60, 45] == 0 and alpha[5, 5] == 0
    assert tuple(rgba[60, 20]) == BOUNDARY_RGBA              # the visible boundary colour
    # the synthetic cells are separated by paper, so merging them (level 2) changes no silhouette edge
    rgba2 = decode_png(get(server, "/api/levels/2/overlay.png")[2])
    assert np.array_equal(rgba2, rgba)


def test_boundary_overlay_helper():
    lab = np.array([[1, 1, 2], [1, 1, 2], [3, 3, 3]], np.int32)
    out = boundary_overlay(lab)
    assert out.shape == (3, 3, 4)
    assert out[..., 3].tolist() == [[0, 255, 255], [255, 255, 255], [255, 255, 255]]


# ----------------------------------------------------------------------------- regions
def test_regions_json_has_twins_sides_and_stats(server):
    regs = get_json(server, "/api/levels/0/regions")
    assert set(regs) == {"1", "2", "3", "4", "5"}
    assert regs["1"]["twin"] == 3 and regs["3"]["twin"] == 1
    assert regs["2"]["twin"] == 4 and regs["4"]["twin"] == 2
    assert regs["5"]["twin"] is None and regs["5"]["iou"] is None and regs["5"]["side"] == "axis"
    assert {regs[k]["side"] for k in ("1", "2")} == {"L"} and {regs[k]["side"] for k in ("3", "4")} == {"R"}
    assert regs["1"]["iou"] > 0.99
    assert regs["1"]["area"] == 50 * 60 and regs["1"]["bbox"] == [20, 30, 50, 60]
    assert abs(regs["1"]["centroid"][0] - 44.5) < 0.01 and abs(regs["1"]["centroid"][1] - 59.5) < 0.01
    assert all(regs[k]["saved_tile_id"] is None for k in regs)
    assert all(regs[k]["group_index"] is None for k in regs)
    regs1 = get_json(server, "/api/levels/1/regions")
    assert set(regs1) == {"5", "6", "7"} and regs1["6"]["twin"] == 7
    assert get_json(server, "/api/levels/4/regions", expect=404)["error"]


# ----------------------------------------------------------------------------- merge / unmerge
def test_merge_creates_group_and_mirror_group_then_unmerge(server, synthetic_dir):
    j = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 2], "mirror": True})
    assert j["groups"] == [[1, 2], [3, 4]]
    assert j["created"] == [GROUP_ID_BASE, GROUP_ID_BASE + 1]
    regs = get_json(server, "/api/levels/0/regions")
    assert set(regs) == {str(GROUP_ID_BASE), str(GROUP_ID_BASE + 1), "5"}
    assert regs[str(GROUP_ID_BASE)]["twin"] == GROUP_ID_BASE + 1
    assert regs[str(GROUP_ID_BASE)]["group_index"] == 0 and regs[str(GROUP_ID_BASE + 1)]["group_index"] == 1
    # the label PNG carries the 24-bit group ids and is not served from a stale cache
    lab = decode_labels(get(server, "/api/levels/0/labels.png")[2])
    assert set(np.unique(lab).tolist()) == {0, 5, GROUP_ID_BASE, GROUP_ID_BASE + 1}
    assert np.array_equal(lab, Session.load(synthetic_dir).label_map(0))
    assert get_json(server, "/api/session")["groups"] == [[1, 2], [3, 4]]
    # unmerge both (highest index first)
    assert post_json(server, "/api/unmerge", {"group_index": 1})["groups"] == [[1, 2]]
    assert post_json(server, "/api/unmerge", {"group_index": 0})["groups"] == []
    assert set(get_json(server, "/api/levels/0/regions")) == {"1", "2", "3", "4", "5"}
    assert Session.load(synthetic_dir).groups == []


def test_merge_without_mirror_and_validation(server):
    j = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 5], "mirror": False})
    assert j["groups"] == [[1, 5]] and j["created"] == [GROUP_ID_BASE]
    # mirror requested but region 5 has no twin -> only one group
    post_json(server, "/api/unmerge", {"group_index": 0})
    j = post_json(server, "/api/merge", {"level": 0, "region_ids": [2, 5], "mirror": True})
    assert j["groups"] == [[2, 5]] and len(j["created"]) == 1
    post_json(server, "/api/unmerge", {"group_index": 0})
    # tapping a twin pair itself: one group across the axis, no second group
    j = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 3], "mirror": True})
    assert j["groups"] == [[1, 3]] and j["created"] == [GROUP_ID_BASE]
    post_json(server, "/api/unmerge", {"group_index": 0})
    assert "error" in post_json(server, "/api/merge", {"level": 0, "region_ids": [1]}, expect=400)
    assert "error" in post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 99]}, expect=404)
    assert "error" in post_json(server, "/api/merge", {"level": 7, "region_ids": [1, 2]}, expect=404)
    assert "error" in post_json(server, "/api/merge", {"level": "x", "region_ids": [1, 2]}, expect=400)
    assert "error" in post_json(server, "/api/merge", None, expect=400, raw=b"{not json")
    assert "error" in post_json(server, "/api/unmerge", {"group_index": 3}, expect=404)
    assert get_json(server, "/api/session")["groups"] == []


# ----------------------------------------------------------------------------- library
def _library_or_skip(server):
    if server.state.library is None:
        pytest.skip(f"library unavailable: {server.state.library_error}")
    return server.state.library


def test_save_pairs_and_tiles(server, synthetic_dir):
    _library_or_skip(server)
    j = post_json(server, "/api/save", {"level": 0, "region_ids": [1, 3]})
    tiles = j["tiles"]
    assert [t["side"] for t in tiles] == ["L", "R"]
    assert tiles[0]["pair_tile_id"] == tiles[1]["id"] and tiles[1]["pair_tile_id"] == tiles[0]["id"]
    assert tiles[0]["id"].endswith("-L00-r1") and tiles[1]["id"].endswith("-L00-r3")
    assert tiles[0]["png_url"] == f"/api/tiles/{tiles[0]['id']}.png"
    # saving again returns the same tiles instead of failing
    again = post_json(server, "/api/save", {"level": 0, "region_ids": [1, 3]})
    assert [t["id"] for t in again["tiles"]] == [t["id"] for t in tiles]
    # a lone axis region: no pair
    solo = post_json(server, "/api/save", {"level": 0, "region_ids": [5]})["tiles"]
    assert solo[0]["side"] == "axis" and solo[0]["pair_tile_id"] is None
    # the twin of an already-saved tile gets linked to it when saved later on its own
    s = Session.load(synthetic_dir)
    lone = post_json(server, "/api/save", {"level": 0, "region_ids": [2]})["tiles"][0]
    twin = post_json(server, "/api/save", {"level": 0, "region_ids": [4]})["tiles"][0]
    assert twin["pair_tile_id"] == lone["id"]
    # listing + regions now know about the tiles
    lib = get_json(server, "/api/library")["tiles"]
    assert {t["id"] for t in lib} == {tiles[0]["id"], tiles[1]["id"], solo[0]["id"], lone["id"], twin["id"]}
    assert all({"id", "side", "pair_tile_id", "png_url", "area_px", "created_at"} <= set(t) for t in lib)
    regs = get_json(server, "/api/levels/0/regions")
    assert regs["1"]["saved_tile_id"] == tiles[0]["id"] and regs["3"]["saved_tile_id"] == tiles[1]["id"]
    assert get_json(server, "/api/session")["library"]["tile_count"] == 5
    # the stored PNG is exactly the cut tile; render modes are views of it
    status, ctype, body = get(server, tiles[0]["png_url"])
    assert status == 200 and ctype == "image/png"
    rgba, bbox = s.cut_tile(0, 1)
    assert np.array_equal(decode_png(body), rgba)
    for mode in ("greyscale", "outline", "remap"):
        status, ctype, body = get(server, tiles[0]["png_url"] + f"?mode={mode}")
        assert status == 200 and ctype == "image/png", (mode, status, body[:200])
        out = decode_png(body)
        assert out.shape == rgba.shape
        if mode != "outline":                                 # outline keeps only ink pixels in its alpha
            assert np.array_equal(out[..., 3], rgba[..., 3])
    grey = decode_png(get(server, tiles[0]["png_url"] + "?mode=greyscale")[2])
    assert np.array_equal(grey[..., 0], grey[..., 1]) and np.array_equal(grey[..., 1], grey[..., 2])
    assert get(server, tiles[0]["png_url"] + "?mode=sepia")[0] == 400
    assert get(server, "/api/tiles/nope.png")[0] == 404
    assert "error" in post_json(server, "/api/save", {"level": 0, "region_ids": []}, expect=400)
    assert "error" in post_json(server, "/api/save", {"level": 0, "region_ids": [42]}, expect=404)
    # the stored bytes are untouched by the render modes
    assert decode_png(get(server, tiles[0]["png_url"])[2]).tobytes() == rgba.tobytes()


def test_save_merged_group_tile(server):
    _library_or_skip(server)
    created = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 2], "mirror": True})["created"]
    j = post_json(server, "/api/save", {"level": 0, "region_ids": created})
    assert [t["id"].endswith(f"-L00-r{g}") for t, g in zip(j["tiles"], created)] == [True, True]
    assert j["tiles"][0]["pair_tile_id"] == j["tiles"][1]["id"]
    assert j["tiles"][0]["area_px"] == 50 * 60 + 50 * 70
    regs = get_json(server, "/api/levels/0/regions")
    assert regs[str(created[0])]["saved_tile_id"] == j["tiles"][0]["id"]


def test_degrades_without_a_library(server):
    state = server.state
    saved_lib, saved_err = state.library, state.library_error
    state.library, state.library_error = None, "library not implemented: test"
    try:
        assert get_json(server, "/api/session")["library"] is None
        assert get_json(server, "/api/library") == {"tiles": []}
        assert "error" in post_json(server, "/api/save", {"level": 0, "region_ids": [1]}, expect=503)
        assert get(server, "/api/tiles/x.png")[0] == 503
        assert get_json(server, "/api/levels/0/regions")["1"]["saved_tile_id"] is None
    finally:
        state.library, state.library_error = saved_lib, saved_err


def test_post_to_an_unknown_route_does_not_desync_a_kept_alive_connection(server):
    """The body of a POST that gets a 404/405 must be drained, or it is parsed as the next request."""
    import http.client
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    body = json.dumps({"level": 0, "region_ids": [1]})
    conn.request("POST", "/api/session", body=body, headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 405
    r.read()
    conn.request("POST", "/api/nowhere", body=body, headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 404
    r.read()
    conn.request("GET", "/api/session")
    r = conn.getresponse()
    assert r.status == 200
    assert "meta" in json.loads(r.read())
    conn.close()


def test_save_composite_at_any_level_records_its_parts(server):
    """A pinned composite is the same region at every level, so saving it from level 1 stores one tile at
    level 0 together with its component tiles, and the green dot shows at every level."""
    _library_or_skip(server)
    created = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 2], "mirror": True})["created"]
    gid, mirror_gid = created
    j = post_json(server, "/api/save", {"level": 1, "region_ids": [gid]})
    tile = j["tiles"][0]
    assert tile["id"].endswith(f"-L00-r{gid}") and tile["composite"] and len(tile["members"]) == 2
    assert all(m.endswith(("-L00-r1", "-L00-r2")) for m in tile["members"])
    for lv in (0, 1, 3):
        assert get_json(server, f"/api/levels/{lv}/regions")[str(gid)]["saved_tile_id"] == tile["id"]
    lib = get_json(server, "/api/library")["tiles"]
    assert {t["id"] for t in lib} == {tile["id"], *tile["members"]}
    assert sum(t["composite"] for t in lib) == 1
    # the mirror composite links to it as a pair when saved from a coarser level
    j2 = post_json(server, "/api/save", {"level": 2, "region_ids": [mirror_gid]})
    assert j2["tiles"][0]["id"].endswith(f"-L00-r{mirror_gid}") and j2["tiles"][0]["pair_tile_id"] == tile["id"]
    assert len(get_json(server, "/api/library")["tiles"]) == 6


def test_save_twin_composites_together_stores_both_with_components(server):
    _library_or_skip(server)
    created = post_json(server, "/api/merge", {"level": 0, "region_ids": [1, 2], "mirror": True})["created"]
    j = post_json(server, "/api/save", {"level": 0, "region_ids": created})
    a, b = j["tiles"]
    assert a["composite"] and b["composite"] and len(a["members"]) == 2 and len(b["members"]) == 2
    assert a["pair_tile_id"] == b["id"] and b["pair_tile_id"] == a["id"]
    assert len(get_json(server, "/api/library")["tiles"]) == 6
