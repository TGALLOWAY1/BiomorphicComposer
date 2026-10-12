# Biomorphic Composer

Photo of a bilaterally symmetric biomorphic drawing in → a reviewed, mirror-paired, provenance-tagged library of reusable parts ("tiles") out.

V1 covers capture → split → merge → review → library. The composition engine, Procreate export and tagging UI are later versions; see `docs/REEL_BEHAVIOR_SPEC.md` for the long-term flow and `docs/V1_BRIEF.md` for what V1 commits to.

## How it works, in one paragraph

Only black ink is a boundary. The pipeline finds the drawing on the page, makes a downsized working copy, classifies pixels that are both dark and colourless as ink, finds the vertical mirror axis by folding the figure onto itself, floods every ink-free cell out to the ink's centre line (watershed), and then builds the **entire merge hierarchy** up front: neighbouring regions merge in order of how little ink sits on their shared border, with a penalty for merging across the axis. The review slider only chooses where to cut that tree, so it can never invent a boundary. Tapping a region selects its mirror twin with it. Saving writes each tile once as original colour + alpha PNG, with immutable provenance in SQLite; greyscale, outline and palette remap are render modes, never stored.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium      # only needed for `verify` without --skip-ui
```

## Run

```bash
# 1. decompose a photo into a session directory (work/IMG_2295)
.venv/bin/biomorphic ingest samples/IMG_2295.jpeg --debug

# 2. review on your phone or tablet: open http://<laptop-ip>:8765 (use --host 0.0.0.0 for the LAN)
.venv/bin/biomorphic serve work/IMG_2295 --library library

# 3. inspect the library
.venv/bin/biomorphic library list

# verification on the sample: report, contact sheet and UI screenshots in verify_out/
.venv/bin/biomorphic verify samples/IMG_2295.jpeg --map samples/IMG_2298.png
```

Debug helpers: `biomorphic sheet <session>` writes a contact sheet; `biomorphic pairs <session> --level N` prints sides and twins.

## Layout

See `docs/ARCHITECTURE.md` for the module map, the session directory format, the library schema, the HTTP API and the verification checks.

## Tests

```bash
.venv/bin/python -m pytest -q              # fast tests on a synthetic session
.venv/bin/python -m pytest -q -m sample    # slow tests on the real sample photo
```
