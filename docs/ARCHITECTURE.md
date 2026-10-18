# Biomorphic Composer V1 — architecture and contracts

This is the contract every module codes against. If you need to change a contract, change this file in the same commit.

## Goal (V1)

Photo of a drawing in → reviewed, mirror-paired, provenance-tagged tile library out.

1. `biomorphic ingest` runs the decomposition pipeline and writes a **session directory**.
2. `biomorphic serve` runs a local server + self-contained review page (phone/tablet friendly).
3. The **library** on disk: one RGBA PNG per tile (original colour + alpha) and metadata in SQLite.
4. `biomorphic verify` runs the pipeline on the sample and writes a report, a contact sheet and UI screenshots.

Out of scope: composition engine, Procreate export, SAM, deformation, tagging UI.

## Non-negotiables (from the brief)

- Tiles are stored once, as original colour + alpha. Greyscale, outline-only and palette remap are **view-time render modes**, never stored assets.
- Provenance is immutable and lives in a namespace separate from artist-authored tags.
- The artist has final authority on grouping. Automation reduces clicks; it never commits on the artist's behalf.
- Only black ink is a boundary. The slider cuts a precomputed merge tree; it never creates a boundary.
- Simple over clever.

## Package layout

```
biomorphic/
  version.py            PIPELINE_VERSION
  session.py            Session, MergeTree, level label maps, manual groups, region stats   (shared core)
  tiles.py              cut_tile(), png encode/decode helpers                                 (shared core)
  cli.py                argparse front door: ingest | serve | verify | library | sheet | pairs
  pipeline/
    run.py              run_pipeline(photo, out_dir, params) -> Path   (orchestrates the steps below)
    ingest.py           EXIF orientation, figure box, crop with margin, working copy, paper/figure mask
    ink.py              ink mask: dark AND colourless in Lab, cleaned
    axis.py             vertical mirror axis search (+ optional small deskew angle), score, mirrored-mask IoU
    oversegment.py      cells (ink-free connected areas) as markers, watershed to the ink centre line, speck floor
    merge.py            region adjacency graph + full hierarchical merge tree, cost dominated by ink on the border
    pairing.py          sides (L / R / axis) and mirror twins at a level: fold overlap + small residual shift + colour,
                        optimal one-to-one assignment (fold method); the reflect + register method without a fold
    fold.py             the fold: reflection about the axis, affine alignment (ECC) and a smooth residual field
    complete.py         recover faint lines where the fold shows a stitched cell
    debug.py            optional debug images for development
  library/
    db.py               SQLite schema, Library API, provenance immutability (trigger + no API path)
    render.py           view-time render modes: original, greyscale, outline, remap (keyed off mean Lab)
  server/
    app.py              stdlib ThreadingHTTPServer, JSON API, serves static/
    static/index.html   the review UI (vanilla JS + canvas, no build step, touch-first)
  verify/
    run.py              run_verify(photo, map_png, out_dir, ...) -> report
    checks.py           automated checks 1–8
    groundtruth.py      segment the hand-made map, label pieces, shape-match tiles
    contact_sheet.py    contact sheet PNG
    ui_drive.py         Playwright drive of the review UI at phone width, screenshots
    report.py           report.md + report.json
tests/                  pytest; fast tests use the synthetic fixture in conftest.py; sample tests marked `sample`
samples/                IMG_2295.jpeg (source photo), IMG_2298.png (hand-made segmentation map)
docs/                   this file, the reel spec
```

Run everything with the project venv: `.venv/bin/python -m pytest`, `.venv/bin/biomorphic ...` (or `.venv/bin/python -m biomorphic.cli`).
Python 3.11+ compatible code (no 3.12-only syntax). NumPy-vectorised; no per-pixel Python loops over full images.

## Session directory (output of `ingest`, input of everything else)

All coordinates are in **working-copy pixel space** unless stated otherwise.

```
<session_dir>/
  session.json       metadata (schema below)
  working.png        RGB working copy: cropped (and deskewed if angle != 0) copy of the oriented source
  figure_mask.png    uint8 0/255, the figure silhouette (ink + fills, holes filled); tiles cover exactly this
  ink_mask.png       uint8 0/255, cleaned black-ink mask
  fragments.npy      int32 HxW; 0 outside the figure; 1..N = leaf fragment ids (tree leaves)
  merge_tree.json    {"n_leaves": N, "merges": [[a, b, cost, new_id], ...]}
  state.json         UI working state, optional: {"groups": [[leaf_id, ...], ...]}
  pairs_L<i>.json    pairing cache per level, optional, written by Session
  fold.npz           the fold: axis_x, affine (2x3), residual field (1/8 resolution), band half-width; sessions
                     written before the fold step have none and pair with the reflect + register method
  debug/             optional, anything the pipeline wants to dump for humans
```

`merges` are in non-decreasing cost. The i-th merge (0-based) creates node id `N + i + 1`. Node ids: 0 = background, 1..N leaves, N+1..2N-1 internal. Every fragment id in `fragments.npy` is a leaf.

`session.json`:

```json
{
  "schema": 1,
  "pipeline_version": "0.2.0",
  "session_id": "3fa9c1b2",                       // first 8 hex of sha256(source.sha256 + pipeline_version + created_at)
  "created_at": "2026-10-12T10:00:00Z",
  "source": {"path": "samples/IMG_2295.jpeg", "filename": "IMG_2295.jpeg", "sha256": "...",
             "width": 1932, "height": 2576, "exif_orientation": 1},      // width/height AFTER orientation
  "crop":   {"x": 0, "y": 0, "w": 0, "h": 0, "margin_px": 0,
             "figure_box": {"x": 0, "y": 0, "w": 0, "h": 0}},            // oriented-source pixel coords
  "working": {"width": 0, "height": 0, "scale": 1.0, "angle_deg": 0.0}, // scale = working px per source px
  "axis": {"x": 0.0, "score": 0.0, "mirror_iou": 0.0, "angle_deg": 0.0,
           "search": {"x_min": 0, "x_max": 0, "angles": [0.0]}},
  "params": {},                                                          // every threshold used, for reproducibility
  "counts": {"fragments": 0, "cells": 0},
  "levels": [{"index": 0, "n_merges": 0, "n_regions": 0, "cost": 0.0}, ...]   // index 0 = finest
}
```

**Levels.** A level is a prefix of the merge list. Level `i` applies the first `n_merges` merges. The pipeline picks ~24 levels: level 0 is the finest meaningful partition (every ink-enclosed cell its own region), later levels are geometrically spaced in `n_regions` down to about 5. The UI slider moves over level indices.

**Region ids at a level** are tree node ids (the root of each leaf after the prefix of merges). They are stable across levels for regions that did not change, which lets the UI keep a selection while sliding.

**Manual groups** (artist merges, `state.json`) are lists of leaf ids. At any level, every region that contains a leaf of a group is unioned into the group's region, whose id is `1_000_000 + group_index`. Groups never create boundaries.

## Shared core API (`biomorphic/session.py`, `biomorphic/tiles.py`)

```python
tree = MergeTree.from_json(d); tree.roots(n_merges) -> np.ndarray   # root[node_id] after n_merges merges
s = Session.load(session_dir)
s.meta                      # session.json dict
s.working_rgb               # HxWx3 uint8 RGB
s.working_lab               # HxWx3 float32 CIE Lab (L 0..100, a/b centred on 0)  (cached)
s.figure_mask, s.ink_mask   # HxW bool
s.fragments                 # HxW int32
s.levels                    # list of level dicts
s.label_map(level_index, apply_groups=True) -> np.ndarray int32   # region ids per pixel, 0 = background
s.regions(level_index) -> dict[int, RegionInfo]   # area, bbox (x,y,w,h), centroid (cx,cy), mean_lab, leaves (tuple of leaf ids)
s.pairing(level_index) -> {"sides": {id: "L"|"R"|"axis"}, "pairs": [{"a","b","iou","dx","dy"}], "twin": {id: id}}
s.add_group(leaf_ids) -> group_index ; s.remove_group(group_index) ; s.groups
s.cut_tile(level_index, region_id) -> (rgba HxWx4 uint8, bbox)     # alpha is binary 0/255 == region mask
tiles.encode_png(rgba) -> bytes ; tiles.decode_png(bytes) -> rgba ; tiles.sha256(bytes) -> str
```

`pairing` is computed by `biomorphic.pipeline.pairing.pair_regions(label_map, axis_x, lab_image, regions)` and cached per level + groups hash.

## Pipeline (`biomorphic/pipeline`)

Order: ingest → ink (+ gap bridges) → axis → fold → oversegment → complete faint lines → oversegment again (when
lines were added) → leaf pairing → mirror-consistent merge → write session. Each step is a pure function on arrays that returns arrays/dicts; `run.py` wires them and writes files. Every threshold is a named parameter with a default and lands in `session.json["params"]`.

- **ingest**: orient by EXIF; estimate paper from the image border; figure = largest connected component of (dark OR chroma-above-paper) after closing, holes filled; crop with margin; downscale so width ≤ `working_width` (default 1800, never upscale); optional deskew by the axis angle (only if |angle| > 0.3°).
- **ink**: ink = dark AND colourless in Lab relative to the paper (navy pencil has chroma; ink does not). Remove specks, close 1-px gaps. Report the median ink half-thickness (`t0`) for cost normalisation.
- **axis**: candidate vertical axes x in the middle ~60% of the figure box (and small angles, e.g. −3…3° in 0.5° steps) scored by the IoU of the figure mask with its reflection (and/or correlation of the ink mask). Returns x, angle, score, mirrored-mask IoU.
- **oversegment**: markers = connected components of (figure AND NOT ink) with area ≥ speck floor; elevation = distance-to-non-ink inside ink (ridge on the centre line); `skimage.segmentation.watershed(elevation, markers, mask=figure)`; every figure pixel gets a label (assign stragglers to the nearest region). Result: `fragments`, N leaves. Optionally extra seeds inside large cells (off by default).
- **ink** also: a dark pixel is colourless when its chroma is below `ink_rel_chroma_frac` of the most colourful fill
  nearby (colour bleeds into thin black strokes in a phone photo); ink must be thin (black-hat contrast above
  `ink_thin_contrast`) unless truly black, with the small holes that test opens refilled; dark areas far wider than a
  stroke are fills (`ink_fill_width_t0`), hollowed out with a stroke-wide rim kept as their wall; **bridge_gaps** closes
  short gaps where a line stops and continues (from each true line end, a straight bridge ahead over dark pixels),
  keeping only bridges that split a cell into real components (no scraps, no slivers).
- **fold**: every point's twin = reflection about the axis, then an affine map (ECC on blurred ink, left half) and a
  smooth residual displacement field (DIS optical flow on L, Gaussian-smoothed at ~1.4 % of the figure width, clipped).
  That map serves the left of the local centre line; the right uses its exact inverse (fixed-point iteration), so the
  twin of a twin is the point itself and a vertical offset between the halves is undone from either side.
  The local centre line is where a point is its own twin; the centre band is that curve ± `fold_band_frac` of the width.
- **complete**: a cell lying on two or more cells of the other half (folded) gets the faint dark pixels under the
  folded dividing line added to the ink, kept only when each resulting piece lies on its own counterpart cell.
- **merge** (mirror-consistent, `merge_symmetric`): units are twin pairs (from the leaf pairing), central regions and
  unpaired regions; two pairs merge only when both sides are adjacent (two merges, one cost), a central region takes a
  pair only when it touches both twins, an unpaired region joins a neighbour; merges completing a unit are flagged
  `unit_end` and levels are cut only there, so every level stays mirror-consistent. Regions left when no symmetric
  merge remains join by the ordinary cost order (`fallback` in the stats, `fallback_merges` in the counts); only the
  last of those merges is a unit end, so no level is cut with one twin merged and the other not.
- **merge**: region adjacency graph with per-edge `border_len`, `ink_sum` (sum of ink half-thickness along the border), per-region area/perimeter/bbox/centroid/side. Cost is **dominated by ink on the shared border**: `ink_norm = (ink_sum / border_len) / t0`, then small modifiers for how embedded the smaller region is (`border_len / min(perimeter)`), size, and an **axis penalty** for merging regions on opposite sides. Agglomerate with a heap (lazy invalidation) until one region, recording every merge → `merge_tree.json`. Choose levels.
- **pairing** (per level, with a fold): the other half is folded on top, so every region is compared with the regions
  its pixels' twins fall in; IoU of the region with the folded twin after a residual shift of at most `fold_shift_px`,
  times colour factors on the mean-Lab distance and the per-pixel colour difference over the overlap; Hungarian
  assignment; side = `axis` when the region folds onto itself (self-IoU ≥ 0.5) and touches the centre band, else by
  centroid against the local centre line.
- **pairing** (per level, without a fold): side = `axis` if the region's own mirrored self-IoU > 0.5, else by centroid. For each L region, candidates on R with overlapping mirrored bbox; score = IoU after reflecting about the axis plus **local registration** (translation search, optionally ±10 % scale); reject if mean Lab ΔE between the two regions is above a threshold; one-to-one assignment. Returns sides, pairs, twin map.

## Library (`biomorphic/library`)

On disk: `<library_dir>/library.sqlite` and `<library_dir>/tiles/<tile_id>.png`.

Tile id: `<source_stem>-<session_id>-L<level:02d>-r<region_id>` (e.g. `IMG_2295-3fa9c1b2-L07-r412`). Manual groups use the group region id.

```sql
CREATE TABLE sources   (sha256 TEXT PRIMARY KEY, filename TEXT, width INT, height INT, first_seen_at TEXT);
CREATE TABLE tiles     (id TEXT PRIMARY KEY, created_at TEXT, png_path TEXT, png_sha256 TEXT,
                        width INT, height INT, area_px INT, side TEXT,            -- 'L' | 'R' | 'axis'
                        pair_tile_id TEXT, pair_iou REAL,
                        mean_L REAL, mean_a REAL, mean_b REAL);
CREATE TABLE provenance(tile_id TEXT PRIMARY KEY REFERENCES tiles(id) ON DELETE CASCADE,
                        source_sha256 TEXT, source_filename TEXT, session_id TEXT, pipeline_version TEXT,
                        axis_x REAL, axis_angle_deg REAL, level_index INT, n_merges INT, region_id INT,
                        fragment_ids TEXT,                 -- JSON list of leaf ids
                        bbox_x INT, bbox_y INT, bbox_w INT, bbox_h INT,           -- working px
                        crop_x INT, crop_y INT, crop_w INT, crop_h INT,           -- oriented-source px
                        working_scale REAL, working_w INT, working_h INT,
                        working_angle_deg REAL,            -- rotation applied to deskew the working copy
                        user_merged INT, created_at TEXT);
CREATE TABLE tags      (tile_id TEXT REFERENCES tiles(id) ON DELETE CASCADE, tag TEXT, created_at TEXT, PRIMARY KEY (tile_id, tag));
CREATE TRIGGER provenance_immutable BEFORE UPDATE ON provenance BEGIN SELECT RAISE(ABORT, 'provenance is immutable'); END;
```

`Library` API (`db.py`): `open(dir)`, `save_tile(session, level, region_id, *, pair_with=None, pair_iou=None) -> TileRecord` (writes PNG, sources + tiles + provenance rows, in one transaction; raises `TileExists` rather than overwrite a stored tile), `save_pair(session, level, a, b, *, iou=None) -> (TileRecord, TileRecord)` (both tiles plus the link, one transaction), `link_pair(a, b, iou)` (the only UPDATE the API issues, on `tiles`), `get(tile_id)`, `list_tiles(source_sha256=None)`, `tile_path(id)`, `tile_png_bytes(id)`, `add_tag/remove_tag/tags(id)`, `delete_tile(id)` (rows cascade, PNG removed, partner unlinked), `summary() -> {n_tiles, n_sources, dir}`, `provenance_is_immutable() -> bool` (probes an UPDATE inside a rolled-back savepoint; verify check 8). There is deliberately **no** method that updates provenance; the trigger enforces it at the database too.

`render.py`: `render(rgba, mode, palette=None) -> rgba` with modes `original | greyscale | outline | remap`; `mean_lab(rgba)`. `outline` keeps only ink-like pixels (dark and colourless) as black with the tile's alpha. `remap` takes a palette `[{"from": [L,a,b], "to": [L,a,b]}, ...]`, picks the entry nearest the tile's mean Lab and shifts every pixel by that delta. Pure functions; nothing here writes to disk.

## Server + UI (`biomorphic/server`)

`biomorphic serve <session_dir> --library <dir> [--host 127.0.0.1] [--port 8765]`

```
GET  /                                 index.html
GET  /api/session                      {"meta": session.json, "groups": [...], "library": Library.summary() + "tile_count"
                                        (null when the library is unavailable), "library_dir": ..., "n_leaves": N}
GET  /api/working.png
GET  /api/levels/<i>/labels.png        RGB PNG, region id = R | G<<8 | B<<16 (0 = background), groups applied
GET  /api/levels/<i>/overlay.png       RGBA PNG: 2-px region boundaries (ink colour) for drawing over the photo
GET  /api/levels/<i>/regions           {"<id>": {"area","bbox","centroid","side","twin","iou","saved_tile_id","group_index"}}
                                        twin/iou null when unpaired, saved_tile_id null unless that region is in the library,
                                        group_index null unless the region is a manual group
POST /api/merge     {"level": i, "region_ids": [...], "mirror": true}   -> {"groups": [...], "created": [group_ids]}
POST /api/unmerge   {"group_index": k}
POST /api/save      {"level": i, "region_ids": [...]}                   -> {"tiles": [{"id","png_url","side","pair_tile_id"}]}
GET  /api/library                      {"tiles": [...]}
GET  /api/tiles/<id>.png?mode=original|greyscale|outline|remap
```

UI behaviour: one slider = level index (shows the region count); tap a region to toggle it in the selection, its mirror twin co-selects (selected = cyan, twin = orange, axis-regions have no twin); **Merge** unions the tapped regions into one group and, when "mirror merges" is on and every tapped region has a twin, the twins into a second group; **Save** writes every selected region (twins included) to the library and links pairs; a tray shows saved tiles. Phone width first (375 px), large hit targets, no build step, no external assets.

Details as implemented: tapping an orange twin deselects the pair; the selection survives a level change for region ids that still exist; after Merge the new group is selected (its mirror group follows as the twin); **Unmerge** appears when a selected region or twin is a manual group and removes those groups; Save returns tiles that already exist instead of failing and links a lone twin to its partner's existing tile; every error is JSON `{"error": ...}` with a status (400 bad request, 404 unknown level/region/tile, 405 wrong method, 503 library unavailable, 501 render not implemented). Level PNGs are cached per (level, groups) and sent `Cache-Control: no-store`.

### UI test hooks

Stable ids and attributes for Playwright (`tests/test_ui_playwright.py`, `verify/ui_drive.py`); `biomorphic/server/browser.py: launch_chromium(playwright, **kw)` launches Chromium here (default launch, then `$PLAYWRIGHT_CHROMIUM_EXECUTABLE`, then `/opt/pw-browsers/chromium`).

- `#stage` — the visible canvas; it shows the whole working image scaled to the width, so image px map to page px through its bounding box. Attributes: `data-selected` / `data-twins` (comma-separated region ids: tapped regions / their co-selected mirror twins), `data-level` (the level on screen; empty while one loads), `data-ready="1"` once the first level is drawn, `data-busy="1"` during a request.
- `#slider` — range input over level indices. `ArrowLeft`/`ArrowRight` move it (focused or not), `Escape` clears the selection. `#count` is the region count at the shown level as a bare number (`data-approx="1"` while a level is still loading); `#cost`, `#level-idx`, `#level-max`.
- `#btn-merge` (disabled unless ≥ 2 tapped regions), `#mirror-merges` (checkbox, checked by default), `#btn-unmerge` (hidden unless applicable), `#btn-save` (disabled when nothing is selected), `#btn-clear`.
- `#tray` — one `.tile[data-id="<tile id>"]` child per saved tile, newest first, with an `img` from `/api/tiles/<id>.png` and a `.badge.pair` when linked; the empty-state text sits outside `#tray`, so `#tray.children.length` is the tile count. `#tray-count`, `#hdr-lib` (tile count, or "library unavailable"), `#hdr-file`, `#hdr-axis`, `#hdr-session`.
- `#toast` — status messages, shown with `data-show="1"` (hidden through `visibility: hidden`); after Save it reads `Saved N tile(s) to the library`. `#info` is the mono readout under the stage (part ids, area, IoU, "saved").

## Verify (`biomorphic/verify`)

`biomorphic verify samples/IMG_2295.jpeg --map samples/IMG_2298.png --out verify_out [--skip-ui]` writes `report.md`, `report.json`, `contact_sheet.png`, `ui/*.png`. Checks:

1. Partition integrity at every level: label map covers exactly the figure mask; tiles re-composite to the working copy inside the mask (pixel-exact).
2. Ink fidelity: ≥ 90 % of internal region-boundary pixels lie on the (slightly dilated) ink mask; reported per level.
3. Axis near the figure's centre; score and mirrored-mask IoU reported.
4. Granularity: some level has ≈50–75 regions with ≈12 on-axis and < 30 per side; counts at every level reported.
5. Pairing: at that level ≥ 80 % of off-axis regions have a twin.
6. Ground truth: segment the map into pieces, label them, best shape-matching tile per piece (scale-normalised IoU after alignment, mirrored allowed), table + misses.
7. Non-destructive: stored PNG sha256 identical before/after every render mode.
8. Provenance: API has no update path and the trigger aborts an UPDATE.

Thresholds are starting points: when the drawing disagrees, the report says so with the number, rather than tuning to pass.

### Grouping cost (`biomorphic/verify/grouping.py`)

`biomorphic grouping <session> --map <map.png> [--labels] [--out grouping_out]` measures how many gestures the review UI, as built, needs to assemble each chunk of the artist's map: `grouping_report.md` / `.json` and `grouping_sheet.png`.

- **Target**: each map piece is projected onto the working copy through the ground-truth shape matcher's transform, with one global scale (working px per map px, the median over confident matches) pinned so a large chunk cannot anchor on a small region of the same silhouette. The target is the set of leaves mostly under the projection, refined by re-fitting on their union (best-fitting iteration kept). Pieces are placed most confident first; a piece landing on leaves already taken is moved to their twins (level-0 pairing). A placement is *confident* when leaf IoU ≥ 0.7 and the area ratio is within 0.6–1.6; headline totals use confident placements only.
- **Cost**: a region is *clean* when all its leaves lie in the target. Single-level cost = taps on clean regions + one Merge, at the cheapest level where every region touching the target is clean (level 0 always). The mixed plan taps the maximal clean regions coarse to fine, one Merge per visited level, counting level changes. Mirror twins co-select, so twin chunks (by the level-0 pairing) are counted once.
- The sample's result lives in `docs/verification/grouping/`.
