# Biomorphic Composer — behavior spec derived from the motion-graphic reel

Source: `Biomorphic_Composer_Reel.html` (64-second, 8-chapter motion graphic, 1200×840 stage).
Everything below is read directly from the reel's script, data object and embedded images.
Where the reel is explicitly schematic (the merge-tree diagram) or illustrative (the exact
counts), that is noted. This document is the **comparison baseline**: each numbered behavior
is something the codebase either already does, does differently, or does not do yet.

One-line pitch (from the title card): *Split a hand-drawn piece into reusable parts, then build
new pieces from them.* Flow: **capture → split → merge → review → library → compose → export**.

---

## 0. Cross-cutting principles the reel keeps repeating

| # | Principle | Where the reel shows it |
|---|-----------|-------------------------|
| P1 | **Non-destructive everywhere.** The original photo is never edited; work happens on a downsized working copy. Render modes and palette remaps are *views*, never edits to stored tiles. | Ch.1 "working copy", Ch.6 "modes are only views", Ch.8 "recolor without touching the tiles" |
| P2 | **Human decides, machine proposes.** Segmentation over-splits on purpose, the merge slider only chooses a cut, mirror twins are *auto-selected* but "Grouping: you decide", parts are "approved" into the library, anchors are hand-placed before the engine fills the rest. | Ch.3, Ch.4, Ch.5, Ch.6, Ch.7 |
| P3 | **Provenance is locked.** Every library part carries a lock icon and the name of its source drawing. | Ch.6 card footer `🔒 IMG_2295` |
| P4 | **Mirror symmetry is a first-class concept.** An axis is detected once, then used for twin selection, pair naming, mirrored composition, and the dashed axis on the compose canvas. | Ch.2, Ch.5, Ch.6, Ch.7 |
| P5 | **Every technical term gets a one-line plain-language gloss with an analogy.** | Chapter cards: correlation, watershed, merge tree, IoU, render modes, sockets, entropy order, non-destructive |
| P6 | **Progress is always visible and countable.** Checklist steps, a live fragment counter, parts-at-this-cut, Placed 4 / 26, layers 26 / 26. | Ch.1, 3, 4, 7, 8 |

---

## 1. Capture (reel 0:00–0:08)

**What the user does:** photographs a finished drawing (reel example: `IMG_2295.jpeg`, phone photo, 3024 × 4032 px) and hands it to the app.

**What the system does** (shown as a 4-step checklist whose check marks turn green one after another, ~0.75 s apart):

1. Find the drawing on the page → *figure box 2323 × 3656 px*
2. Crop with a margin, centered on the figure
3. Make a working copy → *720 × 1097 px* (long edge ≈ 1097, i.e. the copy is width-normalized to 720 px)
4. Mask out the paper → *keep only the figure*

**UX details worth keeping:** a scan-line sweeps down the photo while corner crop marks tighten from the page edge onto the detected figure box; the step list shows the concrete numbers (pixel sizes) under each step in a monospace sub-line; the card is titled with the source filename.

**Gloss:** *Working copy — a downsized duplicate, like a photocopy you can mark up while the original stays untouched.*

## 2. Find the mirror axis (0:08–0:13)

**System behavior:** slide a candidate **vertical** axis across the working copy (reel sweeps x = 216 → 500 px of 720), and for each position score how well the two halves mirror each other with a correlation coefficient *r*. The peak wins. Reel result: **axis x = 359 px, r = 0.78** (second drawing: axis 365 px).

**UI:**
- On the drawing: a dashed vertical line sweeps left→right with a live tag `r = 0.15 … 0.77`; when it settles on the peak the line becomes solid and the tag reads `axis x = 359 px`.
- Side card "Mirror axis search": a line chart of *r* against candidate axis position (x-axis 250…500 px, y-axis 0…0.8) that draws in as the sweep progresses, a dot riding the curve, then a dashed vertical marker and ring at the peak. Readout tiles: **Axis 359 px · Match r 0.78**.
- After settling, the left half **folds over** (3D page-flip) onto the right half at ~55% opacity to let the user eyeball the match.

**Gloss:** *Correlation (r): fold the page along the line; 1.0 means the halves match exactly, 0 means no relationship.*

**Note:** the reel only shows a vertical axis search. Rotation/tilt of the axis is not shown.

## 3. Split aggressively (0:13–0:19)

**System behavior:** watershed segmentation. Seeds are the centers of open (ink-free) areas; the reel uses **376 seeds**. Walls are the black ink lines only. Each flood stops at ink. Goal stated on the card: *"too many pieces, on purpose."*

**UI:**
- Seeds appear as small yellow dots, radiating outward from the axis center (nearest first).
- The fragment label map floods outward in a growing circle from the axis center over the (dimmed: brightness 0.75, saturation 0.65) photo.
- Card: a big counter animates **0 → 376 fragments**, plus a key/value list: Seeds = centers of open areas · Walls = black ink lines only · Goal = too many pieces, on purpose.

**Gloss:** *Watershed: picture rain filling valleys. The ink lines are ridges, so neighboring pools never mix across them.*

## 4. Merge intelligently (0:19–0:27)

**System behavior:** fragments that share an **ink-free edge** merge first, building a **merge tree** (hierarchical clustering / dendrogram). A **resolution slider** only chooses where to cut the tree; *"no new boundaries get drawn."*

**UI:**
- Card "Merge tree · resolution": big number = **parts at this cut**; a slider with four tick stops labelled **376 · 220 · 128 · 90**; the count interpolates log-linearly between stops as the knob moves.
- A dendrogram with a dashed horizontal **cut** line that tracks the slider; sub-trees below the cut are coloured per resulting region, everything above the cut stays grey. (Reel says this diagram is schematic.)
- On the drawing, the overlay cross-fades between the segmentation at each stop (reel assets `frag` → `L1` → `L2` → `L3`), so the user sees regions coalesce as the knob moves.

**Gloss:** *Merge tree: a family tree of regions. Cut low for many small parts, high for a few big ones. No new boundaries get drawn.*

**Numbers to treat as illustrative:** 376/220/128/90 for drawing 1; drawing 2 goes 491 fragments → 99 parts. After review, drawing 1 ends with **29 approved parts**. (Counting regions in the embedded overlay images gives ≈240 cells in the raw fragment pass and ≈197 / 136 / 98 regions at the three merge levels; the hierarchy is strict, every finer region sits wholly inside one coarser region.)

## 5. Review and pair (0:27–0:34)

**Interaction (the reel's most detailed click path):**
1. Tap a part on the drawing → it highlights in the **selection colour (cyan)**.
2. Its **mirror twin is auto-selected** in the **mirror colour (orange)**, a dashed orange line links the two centroids, and a tag sits on the axis: `mirror twin · IoU 0.75`.
3. The review card lists: `● Part 13 · selected  11,937 px` and `● Part 15 · mirror twin, auto-selected  12,715 px`, then a readout **Mirror match (IoU) 0.75 · Grouping: you decide**.
4. Press **Save pair to tray** (button shows a pressed state: slight scale-down, darker fill).
5. The two cut-outs (transparent-background tiles) **fly from the drawing into the tray** slots with a lift/shadow, and slot labels appear: **`C3`** and **`C5 · mirror`** — i.e. saving assigns library IDs, and the twin is labelled as the mirror.

**Data shown:** per-part pixel area, IoU of a part against its mirrored twin, part index.

**Gloss:** *IoU (intersection over union): flip one shape onto the other and measure how much of their combined area they share.*

## 6. Grow the library (0:34–0:43)

**Transition:** the photo fades out; the final segmentation map stays as a dim ghost with the part IDs labelled at each part's position; then every tile **flies from its map position into a grid card** (so the user sees where each part came from).

**Sources card** (lists every drawing ever captured):
- `IMG_2295` thumbnail · **29 parts approved** · `✓ in library` (green)
- `IMG_4056` thumbnail · `splitting…` (fragment map flashes over the thumb) → **99 parts found** · `waiting for your review` (orange)

**Library grid** (3 × 10 cards in the reel): each card has
- the part **ID** (mono, top-left) — see naming scheme below;
- **🔒 IMG_2295** — lock icon + source drawing (provenance, P3);
- **`↔ A4`** pair link, or **`on axis`** (muted) for unpaired centre parts;
- optional **tag chip** (`#wing`) top-right;
- the tile itself, centred, scaled to fit.
- The last card is a **pending source**: `IMG_4056 · 99 parts · in review`.

**Toolbar:** a segmented control **Original | Greyscale | Outline | Recolor** (render *modes* — views only: Greyscale is a desaturate filter on the colour tile; Outline is a raster **ink layer**, near-black with alpha proportional to how dark the fill pixel is, registered exactly over the fill; Recolor is a **warm-colourway remap** of the same alpha mask) and a **search field**. Typing `wing` dims non-matching cards to 30% and gives matching cards (C1, C7, D1, D7) an accent border and visible `#wing` tag.

**Storage statement:** *Each part is stored once as colour plus transparency, with a locked link back to its source drawing.*

**Part naming scheme (read from tile positions and pairs):**
- **Letter = horizontal band** of the drawing, top to bottom: A (y≈60–210), B (≈245–420), C (≈425–645), D (≈625–1065), E (≈975–1100).
- **Number = position across the band, 1…7, symmetric around 4** (the axis). Pairs are `1↔7`, `2↔6`, `3↔5`; `4` sits on the axis. Reel pairs: A2↔A4, C1↔C7, C2↔C6, C3↔C5, D1↔D7, D3↔D5, E3↔E4.
- **Suffix `L`/`R`** when a pair shares a number (B4L↔B4R, B5L↔B5R): small twin pieces that are true mirror images of each other. Whether L/R means the viewer's or the figure's left is not resolvable from the reel.
- **Suffix `s`** = a **second on-axis piece stacked directly beneath the band's central piece** (B3 collar → B3s goblet under it; C4 collar → C4s goblet; D4 shield → D4s larger mask beneath, which is bigger than D4; E0 bow → E0s cap). Read it as "sub/stem", not "small".
- Band E breaks the 1…7 rule: `E0` is the tiny axial bow at the very bottom and `E3`/`E4` are the flanking horns.
- Indices have gaps (no A1, A5, D2, D6, E1, E2), so IDs behave like **stable identifiers**, not a dense sequence.
- Parts are cut at the **merged-part level, not the raw-fragment level**: some tiles are single cells (C3, C5, D3, D4, D5, E0, B3s), others are merged groups (A3 is the whole crown, ≈27 cells; D1/D7 are whole hip complexes). Mirror twins are cut separately from each half of the drawing, so they are near-mirrors with hand-drawn differences, not one tile flipped.

**Gloss:** *Render modes are views, like changing the lighting on a sculpture. Greyscale, outline and recolor never alter the stored tile.*

## 7. Compose (0:43–0:56)

**Layout (three columns):**
- **Left shelf** of candidate parts (reel: D1, B1, D4, C1, C3, D4s, A2, E0s), each in a slot with its ID.
- **Centre canvas**: a 640 × 980 "paper" with a drafting grid and a **dashed orange mirror axis** down the middle.
- **Right card "Compose"**: mode chips **`mirror on`** (active) and **`assisted`**; an **Anchors · locked** list; a **Generate** button; progress **Placed 4 / 26** with bar; **Socket queue · fewest options first** as a row of chips.

**Interaction:**
1. Drag **D1** from the shelf onto the canvas (lift: +7% scale and shadow; drop: settles). Because mirror is on, **D7 pops in mirrored** across the axis (scale 1.12 → 1). Lock badges appear on both; anchor row reads **`D1 ↔ D7 · outer frame`**.
2. Drag **B1** onto the axis → lock → **`B1 · head`**.
3. Drag **D4** onto the axis → lock → **`D4 · spine`**.
4. Press **Generate**. The engine fills the remaining open **sockets** in **entropy order** (the socket with the fewest valid options first), one unit every ~0.44 s: `C4, D4s, C4s, C3↔C5, D3↔D5, C2↔C6, C1↔C7, B3, A2↔A4, E0s, E3↔E4, E0, B5L↔B5R, B4L↔B4R`. Paired units place both twins at once, mirrored.
5. Each placement draws an **accent line from the nearest already-placed part to the new one** (the attachment), with an expanding ring pulse at the new part; the tile pops in with a back-ease overshoot (1.22 → 1). Queue chips go outlined ("now") then filled ("done"). Progress counts up to **26 / 26**. The attachment lines fade once generation completes.

**Glosses:** *Sockets are attachment points, like LEGO studs.* *Entropy order fills the spot with the fewest valid options first, the way you'd solve a Sudoku.*

**Implementation hint:** sockets + entropy-first collapse + mirrored constraint propagation is the vocabulary of Wave-Function-Collapse-style constraint solvers.

**Composition data the reel uses** (26 placements, canvas 640 × 980, axis x = 320): see `docs/reel-data.json` → `comp` (id, centre x/y, scale, z-order, mate, side).

## 8. Recolor and export (0:56–1:04)

**Palette remap card** — *"Keyed off each tile's mean Lab colour"*:
- **Drawn:** `#3f79ab` `#a9dcc1` `#b4b6ba` `#1f3c63`
- **Remapped:** `#b0493a` `#e7c272` `#c9bcae` `#5e2a2a`
- The composed tiles cross-fade to their recoloured versions, staggered top-to-bottom. Stored tiles are untouched (P1).

**Export card:** **Procreate** — *Layered PSD · 26 layers, one per part*; progress bar with `writing layers… 17 / 26` → `26 layers ready for Procreate`. While writing, the canvas tilts into a **3D exploded layer view** (each part lifted by its z-order) and settles back.

**End card:** *Draw a piece. Keep its parts. Build the next one from them.* `capture → split → merge → review → library → compose → export`.

**Gloss:** *Non-destructive: like a coloured gel over a stage light. Pull it off and the original colour is still there.*

---

## 9. Implied data model

```
Source        { id (filename), original photo, figure box, working copy (720 px wide),
                paper mask, axis_x, axis_r, status: splitting | in review | in library,
                counts: fragments, parts at cut, approved }
MergeTree     { per source: leaf fragments, merge order with edge-cost, current cut }
Part / Tile   { id (band letter + position + suffix), source id (locked), bbox on working copy,
                RGBA tile (colour + alpha, feathered edge, ink contour kept),
                outline variant (raster ink layer, same alpha canvas),
                recolour variant (same alpha, remapped colours), area px,
                mate id | null ("on axis"), mirror IoU, tags [] }
Pair          { a, b, iou }  — twin relationship across the axis
Library       { parts[], sources[] }  — search by id / tag, render mode is a view flag
Composition   { canvas size, axis x, placements[{part, cx, cy, scale, z, locked, mate, side}],
                mirror on/off, assisted on/off, socket queue }
Palette       { drawn swatches (mean Lab per tile cluster), remapped swatches }  — view only
Export        { target: Procreate, format: layered PSD, one layer per placed part }
```

## 10. Visual language (from the reel's CSS)

- Light and dark themes via tokens. Accent blue `#2a73b3`, selection cyan `#139fca`, mirror orange `#d98d12`, ok green `#3f9a72`, ink `#162131`, muted `#5a6878`.
- Display font **Syne** (700/800) for titles and big numbers; **Recursive** variable font for body, and in MONO/CASL mode for IDs, labels and readouts.
- Cards: 12 px radius, 1 px line border, soft shadow; eyebrow labels in 12 px uppercase mono with 0.14 em tracking; big numbers in tabular figures.
- Drafting-board stage: 120 px grid with cell labels A1…G10, echoing the band-letter + number part IDs.
- Chips (outlined / filled / "now"), a 4-way segmented control, lock badges for anchors and provenance, ring pulses on placement, dashed orange axis.

---

## 11. What the embedded images show (visual taxonomy)

Read from the 99 decoded images; it confirms the pipeline above and fixes a few details.

**The artworks.** Both are hand-drawn, bilaterally symmetric "totems" of interlocking bone-like biomorphic forms (pelvis, vertebra, scapula, goblet and claw shapes), outlined in heavy black ink and filled with coloured pencil/marker in a strictly cool palette: cobalt and steel blue, teal, pale mint green with hatching, warm silver-grey. They are phone photos (uneven lighting, faint pencil grid). Every colour cell is enclosed by a closed ink contour, which is exactly why ink-as-walls watershed works. Left/right correlation measured on the images: 0.77 (drawing 1), 0.69 (drawing 2).

**Band roles in drawing 1** (what the letters mean on this piece): A = crown, B = upper torso and collar, C = middle band of paired limbs, D = lower band / wing-like hips, E = the small bow/knot at the bottom.

**Pipeline layers as they appear on screen:**
- *Fragment pass* (`frag`): amber contour lines (~2–3 px) on full transparency, outer silhouette plus every cell edge, including slivers and specks typical of raw OpenCV output.
- *Merge levels* (`L1`, `L2`, `L3`): pastel random-colour label maps at ~47% fill alpha with lighter boundary lines, designed to composite over the dimmed photo; strict hierarchy between levels.
- *Selection* (`selA`, `selB`): one region filled solid with a soft ~12 px glow halo; cyan for the tapped part, orange for its mirror twin.
- *Cut-outs* (`cutA`, `cutB`): RGB pixel-identical to the photo, alpha from the segmentation mask with a slightly feathered edge, ink outline included. These two reappear in the library as C3 and C5.

**Tile variants (`t_`, `t_…_ol`, `r_`)** share one canvas per ID. `t_` is the colour fill with true transparency and the pencil grain kept. `_ol` is the ink layer (near-black, alpha tracks pixel darkness, interiors transparent). `r_` is a recolour, **not a mirror**: identical alpha, colours remapped cool → warm (blues → brick red/maroon, teal → red-orange, mint → ochre, grey → warm grey), roughly a hue reflection rather than a uniform rotation. Tile pixel size is a layout choice (cut at ~0.7× of the 720 px working copy), not a property of the part.

**What it says about behaviour:** parts are indexed around the axis (twin for most slots, one axial piece per band, stacked `s` pieces along the axis), which is what lets the composer snap and mirror placements along a spine. Transforms in the reel are applied at render time, never baked into tiles. All confirmed tiles come from drawing 1; drawing 2 contributes only its fragment and level overlays, consistent with its "waiting for your review" status.

## 12. Comparison checklist (fill in against the codebase)

| # | Reel behavior | Codebase today | Verdict (keep / adopt / adapt) |
|---|---------------|----------------|--------------------------------|
| 1.1 | Auto-find figure box on the page, crop with margin | | |
| 1.2 | 720 px working copy; original untouched | | |
| 1.3 | Paper mask (keep only the figure) | | |
| 1.4 | Step checklist with concrete numbers | | |
| 2.1 | Vertical axis search by mirror correlation, peak wins | | |
| 2.2 | Live r readout + curve chart + fold preview | | |
| 3.1 | Watershed from open-area seeds, ink as walls, deliberate over-split | | |
| 3.2 | Live fragment counter / seed visualisation | | |
| 4.1 | Merge tree over ink-free shared edges | | |
| 4.2 | Resolution slider = cut height; count at cut; overlay updates live | | |
| 4.3 | Dendrogram with moving cut line | | |
| 5.1 | Tap part → mirror twin auto-selected, IoU shown | | |
| 5.2 | User confirms grouping; "Save pair to tray" | | |
| 5.3 | Tray assigns IDs (`C3`, `C5 · mirror`) and shows cut-outs | | |
| 6.1 | Sources list with per-source status (splitting / in review / in library) | | |
| 6.2 | Library cards: ID, locked source, ↔ pair or "on axis", tags | | |
| 6.3 | Render modes Original / Greyscale / Outline / Recolor as views (ink layer + recolour share the tile's alpha) | | |
| 6.4 | Search by id/tag with dim-and-highlight | | |
| 6.5 | Band-letter + symmetric-number + L/R/s naming scheme | | |
| 6.6 | Tiles fly from map position to grid (shows provenance) | | |
| 7.1 | Shelf → canvas drag with mirror-on auto twin | | |
| 7.2 | Lock anchors; anchor list with roles (outer frame / head / spine) | | |
| 7.3 | Generate: socket-based fill, entropy order, mirrored units | | |
| 7.4 | Placement graph lines + ring pulse + progress N / 26 + queue chips | | |
| 8.1 | Palette remap keyed off mean Lab colour, view-only | | |
| 8.2 | Export layered PSD for Procreate, one layer per part, progress | | |
| 8.3 | Exploded 3D layer preview | | |
| P1–P6 | Cross-cutting principles above | | |

## 13. Things the reel leaves open (decide with the user)

- Only a **vertical** axis is searched; no rotation correction before the search.
- What "approved" means between the 90-part cut and the 29 parts in the library (reject noise/paper? merge further by hand?).
- Whether tags (`#wing`) are typed by the user or suggested.
- What a **socket** is concretely (contact points on tile boundaries? band/row slots? attachment to nearest placed part?) and what makes an option "valid".
- Whether "assisted" (vs. fully manual) is the only compose mode.
- Export: PSD is shown; native `.procreate` or PNG-per-layer are not mentioned.
- The reel's numbers (376/220/128/90, IoU 0.75, r 0.78) come from a quick OpenCV run and are illustrative.
- Whether `L`/`R` in part IDs means the viewer's left/right or the figure's.
- Whether the Outline mode should stay a raster ink layer (as in the reel) or become a vector trace.
