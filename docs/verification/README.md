# Verification artefacts for the sample

Output of `biomorphic verify samples/IMG_2295.jpeg --map samples/IMG_2298.png` on this branch, kept here so the hand-back is reviewable without re-running it. `report.md` / `report.json` are verbatim; `contact_sheet.jpg` and `gt_matches.jpg` are downscaled copies of the PNGs the command writes; `ui/` holds the Playwright screenshots at half size.

Re-run with the command above to regenerate the full-resolution files in `verify_out/`.

`stages/` holds screenshots of the page's eight stages on the same sample (1280 × 860, plus two at phone width), taken by walking the page in a headless browser: Capture, Mirror axis (folded), Split, Merge, Review (a pair selected; then a tray of saved parts), Library (grid, a search for a tag, a part's sheet) and the locked Compose stage. Compare them with the reel through `docs/REEL_BEHAVIOR_SPEC.md`, section 12.
