# Biomorphic Composer — V1 build brief

## Context

I'm an artist. My drawings are bilaterally symmetric biomorphic figures. A black ink pen marks every component boundary; navy/blue pencil and teal/green marker are shading and color *inside* components. I want to turn each finished drawing into a library of reusable components ("tiles") that I'll later compose into new pieces.

This repo is empty. Earlier throwaway prototypes proved the approach works; you're building the real thing.

## V1 goal

**Photo of a drawing in → reviewed, mirror-paired, provenance-tagged tile library out.**

1. A CLI that ingests a photo and runs the decomposition pipeline.
2. A local review UI (phone/tablet friendly, runs in a browser) where I choose granularity with one slider, tap components (the mirror twin co-selects), merge a few by hand if needed, and save to the library.
3. A library on disk: each tile is a PNG of original color + alpha, with metadata in SQLite.

**Out of scope for V1:** the composition engine (sockets, placement graph, entropy-ordered assembly), Procreate export, SAM, deformation, and a tagging UI. The long-term vision is: lock a few anchor tiles on a canvas, let an engine fill the rest symmetrically, recolor, and export layers to Procreate. Leave clean seams for that, but don't build it.

## What the prototypes learned (constraints, not a recipe)

- **Only black ink is a boundary.** Ink = dark *and* colorless (low chroma in Lab). Navy pencil and color gradients are not boundaries; treating them as such was the #1 failure in prototypes (jagged false edges inside real components).
- **Split aggressively, then merge intelligently.** Over-segment first with regions grown out to the ink's center line so neighbors share a border with no gutter. Then build the *entire* merge hierarchy up front. Merge cost should be dominated by how much ink sits on the shared border; merges across the symmetry axis are penalized.
- **The slider cuts the precomputed tree. It never creates a boundary.** Every boundary must exist before merging starts.
- **Symmetry is approximate.** It's hand-drawn: find the vertical axis that best maps the figure onto its reflection, then pair mirror components with local registration. A rigid reflection alone tops out around IoU ≈ 0.5 on my work. Use Lab color difference to reject false geometric matches.
- Drop specks with an area floor.
- Target granularity on a typical piece: roughly 12 components along the central axis and fewer than 30 per side (≈50–75 total). Not hundreds of fragments.

## Non-negotiables

- Tiles are stored once, as original color + alpha. Greyscale, outline-only, and palette remap (keyed off each tile's mean Lab color) are view-time render modes, never stored assets.
- Provenance (source image hash, axis, region ids, bbox, pipeline version) is immutable and lives in a namespace separate from artist-authored tags.
- I have final authority on grouping. Automation reduces clicks; it never commits on my behalf.
- Simple over clever. If you're about to add a mechanism that invents boundaries or a layer of abstraction V1 doesn't need, stop and tell me instead.

## Stack

Python 3.11+, OpenCV, scikit-image, NumPy, SQLite, pytest. Keep the review UI dependency-light (a self-contained page plus a minimal local server is fine). Your call on structure.

## Sample data

I'll put these in `samples/`:

- `IMG_2295.jpeg` — the source drawing. 4032×3024 stored sideways; correct the EXIF orientation (3024×4032 portrait).
- `IMG_2298.png` — my hand-made segmentation map of that same drawing: the components drawn exploded on a lettered grid (A1–E7), ~29 labeled pieces with left/right mirror pairs. **This is ground truth for what "a component" means to me.** Positions differ from the source (it's exploded), so compare by shape, not location.

## Verification — V1 is done when these pass

Build a `verify` command that runs on the sample and writes a report. Thresholds are starting points from the prototypes; if the drawing genuinely disagrees, report the number and why rather than tuning to pass.

**Automated**

1. **Partition integrity:** at every slider level, tiles cover the figure with no gaps or overlaps, and re-compositing all tiles reproduces the cropped original inside the figure mask.
2. **Ink fidelity:** ≥ 90% of tile-boundary pixels fall on the black-ink mask.
3. **Axis:** detected axis is near the figure's visual center; report its score and the mirrored-mask IoU.
4. **Granularity:** some slider level yields ≈50–75 components (≈12 on-axis, < 30 per side). Report the count at every level.
5. **Pairing:** at that level, ≥ 80% of off-axis components have a mirror partner.
6. **Ground truth:** for each labeled piece in the segmentation map, find the best shape-matching tile and report a table (map label → tile id → score). Aim for a clear majority matched; list the misses.
7. **Non-destructive:** stored tile PNGs hash identically before and after every render mode.
8. **Provenance:** the library API cannot modify provenance fields.

**Visual (look at these yourself before handing off)**

- A contact sheet: source with tile outlines at three slider levels, the axis overlay, mirror pairs color-coded, and the ground-truth matches side by side with the segmentation map. Outlines must follow drawn black lines, not shading edges.
- Drive the review UI headlessly at phone width (e.g. Playwright): move the slider, tap a component, confirm the twin highlights, save, confirm the tile and its provenance land in the library. Screenshot each step.

## How to work

- Small commits; a README with setup and run instructions.
- When something regresses, diagnose the cause and make a targeted fix rather than a rewrite.
- Hand back: what you built, the verify report, the contact sheet and UI screenshots, and the places you're least confident.
