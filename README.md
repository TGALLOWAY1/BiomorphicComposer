# Biomorphic Composer

Photo of a bilaterally symmetric biomorphic drawing in → a reviewed, mirror-paired, provenance-tagged library of reusable parts ("tiles") out.

V1 covers capture → split → merge → review → library. The composition engine and Procreate export are later versions; `docs/REEL_BEHAVIOR_SPEC.md` describes the long-term flow and `docs/V1_BRIEF.md` what V1 commits to. `docs/ARCHITECTURE.md` is the contract every module follows.

## How it works

Think of the drawing as a map of countries whose borders are drawn in black ink.

1. **Capture.** The photo is oriented from its EXIF tag, the drawing is found on the page, cropped with a margin, and downsized to a working copy (at most 1800 px wide). The original is never touched.
2. **Ink.** Only pixels that are both dark *and* colourless, measured relative to the paper's own tint, count as ink. Navy pencil is dark but blue, so it is not a boundary. A thin black stroke that picked up the colour of the fills beside it still counts (it is far less colourful than they are); a dark fill as dark as the ink does not (it is far wider than any stroke). Short gaps where a line stops and continues are bridged, but only when the bridge closes off a real component.
3. **Axis and fold.** Every vertical line through the figure is scored by folding the drawing onto itself (mirrored-mask IoU); the best one is the symmetry axis. Hand-drawn halves never match exactly, so the fold then aligns the mirrored half onto the other (tilt, stretch and a gentle, smooth bend): every point knows where its twin is, and the centre line may lean and wander inside a tolerance band.
4. **Split.** Every ink-free cell becomes a seed and floods outward to the ink's centre line (a watershed), so neighbouring cells meet with no gutter and every figure pixel belongs to exactly one cell. Where a cell lies on top of two cells of the folded other half, a faint line drawn on this side too is recovered and the cell splits.
5. **Merge.** The whole merge hierarchy is built up front: neighbouring regions merge in order of how much ink sits on their shared border (normalised by the pen's line weight), nudged by how embedded the smaller region is and by size. Mirror twins merge together, so every level of the slider stays mirror-consistent. The review slider only chooses where to cut this tree; it can never invent a boundary.
6. **Review.** Tap a region and its mirror twin selects with it. Twins are found on the fold: each region is compared with the region it lands on, allowing a small nudge, by shape overlap and colour. Merge a few by hand if you need to; the artist decides, nothing is committed automatically.
7. **Library.** Each saved tile is one PNG of original colour + alpha, with immutable provenance (source hash, axis, region ids, bounding box, pipeline version) in SQLite, separate from artist-authored tags. Greyscale, outline-only and palette remap are view-time render modes, never stored.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium      # only for `verify` without --skip-ui and the UI tests
```

If Playwright cannot find a browser, point `PLAYWRIGHT_CHROMIUM_EXECUTABLE` at a Chromium binary.

## Run

```bash
# 1. decompose a photo into a session directory (work/IMG_2295); --debug also writes overlays
.venv/bin/biomorphic ingest samples/IMG_2295.jpeg --debug

# 2. review on your phone or tablet: open http://<laptop-ip>:8765 (use --host 0.0.0.0 for the LAN)
.venv/bin/biomorphic serve work/IMG_2295 --library library

# 3. inspect the library
.venv/bin/biomorphic library list
.venv/bin/biomorphic library tag <tile-id> wing

# verification on the sample: report.md, report.json, contact_sheet.png and ui/*.png in verify_out/
.venv/bin/biomorphic verify samples/IMG_2295.jpeg --map samples/IMG_2298.png
```

Debug helpers: `biomorphic sheet <session>` writes a contact sheet of a session; `biomorphic pairs <session> --level N` prints sides and twins; `biomorphic ingest --param KEY=VALUE` overrides any pipeline parameter (they are all listed in `session.json`).

## The page

The page follows the reel stage by stage (`docs/REEL_BEHAVIOR_SPEC.md` has the reel's behaviour and, in section 12, a row-by-row comparison with the app). Think of it as a factory tour: the first three stops show what the machine already did to your photo, the next three are where you work, and the last two are rooms that are not built yet.

1. **Capture**, 2. **Mirror axis**, 3. **Split**: what the pipeline found, with its real numbers: the crop, the axis search curve and a fold-to-compare, the fragment map.
4. **Merge**: one slider sets the granularity (it opens near 60 parts), with the merge tree and its cut line beside it.
5. **Review**: tap a part to select it; its twin is selected with it (cyan and orange). With **paint** on (the default) a drag across several parts selects them all in one stroke; tap a part to add or remove it. **Merge** joins the selected parts into one composite and, with *mirror merges* on, makes its mirror composite too (the parts under the composite folded across the axis). A composite is pinned: it stays exactly the parts you grouped at every slider position. **Save … to tray** writes every selected part to the library as a tile, a composite together with its component tiles, links the pair and names the tiles (`C3 ↔ C5`: a letter for how far down the drawing, a number for how far across, 4 on the axis). Tapping a tray card jumps back to that part.
6. **Library**: every stored part with its locked source, pair and tags; Original / Greyscale / Outline / Recolor as views; search; a sheet per part to rename it and tag it.
7. **Compose** and 8. **Recolor and export** are marked with a lock: later versions.

On a phone the page is one column with the slider and buttons pinned to the bottom. Open a stage directly with `http://<host>:8765/#review` (or `#library`, `#merge`, …).

## Layout

```
biomorphic/pipeline   ingest, ink, axis, oversegment, merge, pairing, run, debug
biomorphic/session.py shared core: merge tree cuts, level label maps, manual groups, region stats
biomorphic/library    SQLite library (db.py), part names (naming.py) and render modes (render.py)
biomorphic/server     stdlib HTTP server (app.py), the single-file page (static/index.html) and its fonts
biomorphic/verify     the eight checks, ground-truth matching, contact sheet, UI drive, report
samples/              the sample photo, the hand-made segmentation map and its curated labels
docs/                 architecture contract, V1 brief, reel-derived behaviour spec
```

## Tests

```bash
.venv/bin/python -m pytest -q              # fast tests on a synthetic session (plus two sample tests)
.venv/bin/python -m pytest -q -m sample    # only the tests that run on the real sample photo
```
