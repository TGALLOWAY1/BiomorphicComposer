# Biomorphic Composer

Photo of a bilaterally symmetric biomorphic drawing in → a reviewed, mirror-paired, provenance-tagged library of reusable parts ("tiles") out.

V1 covers capture → split → merge → review → library. The composition engine, Procreate export and tagging UI are later versions; `docs/REEL_BEHAVIOR_SPEC.md` describes the long-term flow and `docs/V1_BRIEF.md` what V1 commits to. `docs/ARCHITECTURE.md` is the contract every module follows.

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

# grouping cost: how many taps / merges / slider moves each chunk of the artist's map costs in the review UI
.venv/bin/biomorphic grouping work/IMG_2295 --map samples/IMG_2298.png
```

Debug helpers: `biomorphic sheet <session>` writes a contact sheet of a session; `biomorphic pairs <session> --level N` prints sides and twins; `biomorphic ingest --param KEY=VALUE` overrides any pipeline parameter (they are all listed in `session.json`).

## Review page

One slider sets the granularity (it opens near 60 parts). Tap a part to select it; its twin is selected with it (cyan and orange). **Merge** joins the tapped parts into one, and with *mirror merges* on, their twins into another. **Save** writes every selected part to the library as a tile and links the pair. The tray shows what is saved; tapping a tray card jumps back to that part.

## Layout

```
biomorphic/pipeline   ingest, ink, axis, oversegment, merge, pairing, run, debug
biomorphic/session.py shared core: merge tree cuts, level label maps, manual groups, region stats
biomorphic/library    SQLite library (db.py) and render modes (render.py)
biomorphic/server     stdlib HTTP server (app.py) and the single-file review page (static/index.html)
biomorphic/verify     the eight checks, ground-truth matching, contact sheet, UI drive, report
samples/              the sample photo, the hand-made segmentation map and its curated labels
docs/                 architecture contract, V1 brief, reel-derived behaviour spec
```

## Tests

```bash
.venv/bin/python -m pytest -q              # fast tests on a synthetic session (plus two sample tests)
.venv/bin/python -m pytest -q -m sample    # only the tests that run on the real sample photo
```
