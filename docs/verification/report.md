# Biomorphic Composer - verification report

Generated 2026-10-12T17:16:55Z. Photo `samples/IMG_2295.jpeg`, map `samples/IMG_2298.png`, session `verify_out/session` (id 68adae2e), pipeline version 0.1.0.

## Summary: FAIL: 9 passed; 1 failed (ground_truth)

| check | name | status | summary |
|---|---|---|---|
| 0. Pipeline run | `pipeline` | PASS | pipeline wrote verify_out/session |
| 1. Partition integrity | `partition_integrity` | PASS | 24 levels: label map == figure mask at every level, tiles re-composite pixel-exactly |
| 2. Ink fidelity | `ink_fidelity` | PASS | level 7: 100.0% of 60979 boundary px on ink (dilated 1 px); threshold 90%; over all levels min 100.0% / max 100.0% |
| 3. Axis | `axis_check` | PASS | axis x=812.5 is -1.0 px (-0.1% of width 1485) from the figure centre 813.5; allowed +/-15%; score=0.975 mirror_iou=0.975 (recomputed 0.975) |
| 4. Granularity | `granularity` | PASS | picked level 7: 68 regions (13 on-axis, 26 L, 29 R); levels in band [7, 8] |
| 5. Pairing | `pairing_check` | PASS | level 7: 46/55 off-axis regions have a twin (83.6%; threshold 80%); 9 unpaired |
| 6. Ground truth | `ground_truth` | FAIL | map IMG_2298.png: one-to-one 10/30 pieces matched at level 7 (33%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (215 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (215 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (68 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.72 at level 7, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 14, B5-L 0.79 at level 8, B5-R 0.78 at level 5, C4-T 0.76 at level 0, C1 0.70 at level 10, C2 0.76 at level 0, C6 0.77 at level 0, C7 0.71 at level 0, D1 0.74 at level 0, D4-B 0.78 at level 7, D7 0.73 at level 0, E4-B 0.76 at level 0. |
| 7. Non-destructive render modes | `non_destructive` | PASS | 3 tiles x 4 render modes: stored PNG sha256 unchanged, no files added |
| 8. Provenance immutable | `provenance_immutable` | PASS | provenance_is_immutable()=True; public members mentioning provenance: ['provenance_is_immutable']; direct UPDATE on 3 rows -> IntegrityError: provenance is immutable |
| UI drive (Playwright) | `ui_drive` | PASS | 5 screenshots; selected='126' twins='127'; saved ['IMG_2295-68adae2e-L07-r126', 'IMG_2295-68adae2e-L07-r127'] with provenance |

## 0. Pipeline run (`pipeline`) - PASS

pipeline wrote verify_out/session

- session_dir: verify_out/session
- photo: samples/IMG_2295.jpeg
- params: {}
- synthetic: False

## 1. Partition integrity (`partition_integrity`) - PASS

24 levels: label map == figure mask at every level, tiles re-composite pixel-exactly

| level | regions | cover ok | cover mismatch px | recomposite ok | diff px | gap px | overlap px |
|---|---|---|---|---|---|---|---|
| 0 | 215 | yes | 0 | yes | 0 | 0 | 0 |
| 1 | 183 | yes | 0 | yes | 0 | 0 | 0 |
| 2 | 155 | yes | 0 | yes | 0 | 0 | 0 |
| 3 | 132 | yes | 0 | yes | 0 | 0 | 0 |
| 4 | 112 | yes | 0 | yes | 0 | 0 | 0 |
| 5 | 95 | yes | 0 | yes | 0 | 0 | 0 |
| 6 | 81 | yes | 0 | yes | 0 | 0 | 0 |
| 7 | 68 | yes | 0 | yes | 0 | 0 | 0 |
| 8 | 58 | yes | 0 | yes | 0 | 0 | 0 |
| 9 | 49 | yes | 0 | yes | 0 | 0 | 0 |
| 10 | 42 | yes | 0 | yes | 0 | 0 | 0 |
| 11 | 36 | yes | 0 | yes | 0 | 0 | 0 |
| 12 | 30 | yes | 0 | yes | 0 | 0 | 0 |
| 13 | 26 | yes | 0 | yes | 0 | 0 | 0 |
| 14 | 22 | yes | 0 | yes | 0 | 0 | 0 |
| 15 | 18 | yes | 0 | yes | 0 | 0 | 0 |
| 16 | 16 | yes | 0 | yes | 0 | 0 | 0 |
| 17 | 13 | yes | 0 | yes | 0 | 0 | 0 |
| 18 | 11 | yes | 0 | yes | 0 | 0 | 0 |
| 19 | 10 | yes | 0 | yes | 0 | 0 | 0 |
| 20 | 8 | yes | 0 | yes | 0 | 0 | 0 |
| 21 | 7 | yes | 0 | yes | 0 | 0 | 0 |
| 22 | 6 | yes | 0 | yes | 0 | 0 | 0 |
| 23 | 5 | yes | 0 | yes | 0 | 0 | 0 |

## 2. Ink fidelity (`ink_fidelity`) - PASS

level 7: 100.0% of 60979 boundary px on ink (dilated 1 px); threshold 90%; over all levels min 100.0% / max 100.0%

Ink mask dilated by 1 px; picked level 7.

| level | boundary px | on ink px | off ink px | fraction on ink |
|---|---|---|---|---|
| 0 | 75513 | 75487 | 26 | 100.0 % |
| 1 | 73572 | 73551 | 21 | 100.0 % |
| 2 | 72059 | 72044 | 15 | 100.0 % |
| 3 | 70575 | 70571 | 4 | 100.0 % |
| 4 | 69321 | 69318 | 3 | 100.0 % |
| 5 | 66856 | 66856 | 0 | 100.0 % |
| 6 | 64614 | 64614 | 0 | 100.0 % |
| 7 | 60979 | 60979 | 0 | 100.0 % |
| 8 | 57229 | 57229 | 0 | 100.0 % |
| 9 | 54383 | 54383 | 0 | 100.0 % |
| 10 | 50967 | 50967 | 0 | 100.0 % |
| 11 | 46437 | 46437 | 0 | 100.0 % |
| 12 | 44418 | 44418 | 0 | 100.0 % |
| 13 | 41914 | 41914 | 0 | 100.0 % |
| 14 | 38574 | 38574 | 0 | 100.0 % |
| 15 | 37068 | 37068 | 0 | 100.0 % |
| 16 | 35400 | 35400 | 0 | 100.0 % |
| 17 | 32642 | 32642 | 0 | 100.0 % |
| 18 | 28493 | 28493 | 0 | 100.0 % |
| 19 | 26518 | 26518 | 0 | 100.0 % |
| 20 | 24255 | 24255 | 0 | 100.0 % |
| 21 | 23387 | 23387 | 0 | 100.0 % |
| 22 | 20743 | 20743 | 0 | 100.0 % |
| 23 | 19346 | 19346 | 0 | 100.0 % |

## 3. Axis (`axis_check`) - PASS

axis x=812.5 is -1.0 px (-0.1% of width 1485) from the figure centre 813.5; allowed +/-15%; score=0.975 mirror_iou=0.975 (recomputed 0.975)

- axis x = 812.5 px, angle = 0.00 deg
- figure box (working px): x=71 y=70 w=1485 h=2338, centre x = 813.5
- offset from centre: -1.0 px = -0.1 % of the figure width (allowed +/-15.0 %)
- pipeline axis.score = 0.975, axis.mirror_iou = 0.975, mirror IoU recomputed by verify = 0.975

## 4. Granularity (`granularity`) - PASS

picked level 7: 68 regions (13 on-axis, 26 L, 29 R); levels in band [7, 8]

- target band: 50-75 regions, about 12 on-axis, at most 29 per side
- picked level: **7**; levels inside the band: [7, 8]
- the per-level table is below.

## 5. Pairing (`pairing_check`) - PASS

level 7: 46/55 off-axis regions have a twin (83.6%; threshold 80%); 9 unpaired

- level 7: 46 of 55 off-axis regions paired (83.6 %), 13 on-axis regions

Unpaired regions (9), largest first:

| region | side | area px | bbox x,y,w,h |
|---|---|---|---|
| 361 | L | 36178 | 361,1930,368,231 |
| 357 | R | 27942 | 1010,1684,186,342 |
| 53 | L | 20312 | 414,629,201,197 |
| 341 | L | 15211 | 134,81,684,1304 |
| 345 | R | 13496 | 1237,876,319,1299 |
| 347 | R | 11312 | 830,1856,167,133 |
| 76 | R | 10673 | 1035,1017,87,207 |
| 328 | R | 9674 | 818,70,513,812 |
| 73 | R | 7941 | 864,937,118,173 |

Top pairs by IoU (12 of 23):

| a | b | iou | dx | dy | delta E |
|---|---|---|---|---|---|
| 332 | 330 | 0.933 | -24.0 | -4.0 | 1.7 |
| 126 | 127 | 0.886 | -18.0 | 4.0 | 2.3 |
| 51 | 46 | 0.884 | -6.0 | -4.0 | 1.8 |
| 284 | 303 | 0.873 | 3.0 | 10.0 | 6.0 |
| 335 | 307 | 0.865 | -3.0 | -6.0 | 0.3 |
| 322 | 329 | 0.825 | 5.0 | -1.0 | 0.9 |
| 62 | 278 | 0.824 | 10.0 | -7.0 | 1.1 |
| 133 | 134 | 0.813 | -11.0 | 11.0 | 1.2 |
| 325 | 344 | 0.802 | -5.0 | -2.0 | 2.0 |
| 290 | 308 | 0.780 | -14.0 | -2.0 | 0.4 |
| 352 | 148 | 0.773 | -6.0 | 0.0 | 3.7 |
| 40 | 39 | 0.767 | -30.0 | -12.0 | 1.9 |

## 6. Ground truth (`ground_truth`) - FAIL

map IMG_2298.png: one-to-one 10/30 pieces matched at level 7 (33%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (215 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (215 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (68 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.72 at level 7, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 14, B5-L 0.79 at level 8, B5-R 0.78 at level 5, C4-T 0.76 at level 0, C1 0.70 at level 10, C2 0.76 at level 0, C6 0.77 at level 0, C7 0.71 at level 0, D1 0.74 at level 0, D4-B 0.78 at level 7, D7 0.73 at level 0, E4-B 0.76 at level 0.

- map: `samples/IMG_2298.png`; labels: `samples/IMG_2298.labels.json` (loaded)
- 30 map pieces, 68 candidate regions at the picked level 7; a piece matches at scale-normalised shape IoU >= 0.80 (mirror and +/-10 deg allowed)
- labels attached to 30 pieces; label entries with no piece: none; pieces left unlabelled: none
- **one-to-one assignment (headline): 10 of 30 (33.3 %); pass rule >= 50.0 %**
- one-to-one matched fraction if the bar were: 0.60 -> 100%, 0.65 -> 97%, 0.70 -> 67%, 0.75 -> 43%, 0.80 -> 33%, 0.85 -> 27%, 0.90 -> 10%
- greedy best per piece: 11 of 30 (36.7 %); shape IoU min/median/mean/max = 0.647 / 0.775 / 0.785 / 0.956; baseline (median IoU over every piece x region pair) 0.471
- best level over the evaluated levels: 0 (215 regions), one-to-one 15 of 30 (50.0 %)
- pieces that never reach IoU 0.80 at any level: A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B
- why: level 0 (215 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (68 regions), so the picked granularity is coarser than the map's components.
- why: 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.72 at level 7, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 14, B5-L 0.79 at level 8, B5-R 0.78 at level 5, C4-T 0.76 at level 0, C1 0.70 at level 10, C2 0.76 at level 0, C6 0.77 at level 0, C7 0.71 at level 0, D1 0.74 at level 0, D4-B 0.78 at level 7, D7 0.73 at level 0, E4-B 0.76 at level 0.
- regions that are the greedy best of several pieces at once: r73 <- A2, A4; r330 <- B1, B2, B5-L, E5; r315 <- B3-T, C4-T; r81 <- B4-R, C4-B; r49 <- B3-B, C2, C6; r332 <- C1, E3; r356 <- C3, C5; r95 <- D1, D7

Greedy best per piece (margin = IoU minus the runner-up region's IoU):

| map label | best tile (region id) | shape IoU | runner-up | margin | matched | transform |
|---|---|---|---|---|---|---|
| A2 | 73 | 0.715 | 0.665 | 0.051 | no | mirror, +2.5°, shift (-3,+1) |
| A3 | 134 | 0.692 | 0.706 | -0.013 | no | -2.5°, shift (+2,+0) |
| A4 | 73 | 0.714 | 0.674 | 0.040 | no | +5°, shift (-3,-2) |
| B1 | 330 | 0.791 | 0.809 | -0.018 | no | +10°, shift (+1,+2) |
| B2 | 330 | 0.781 | 0.820 | -0.039 | no | -7.5°, shift (+1,+0) |
| B3-T | 315 | 0.647 | 0.669 | -0.023 | no | mirror, -2.5°, shift (-3,+0) |
| B4-L | 126 | 0.686 | 0.742 | -0.056 | no | -2.5°, shift (-1,-6) |
| B4-R | 81 | 0.741 | 0.720 | 0.021 | no | mirror, +7.5°, shift (+0,+3) |
| B5-L | 330 | 0.778 | 0.790 | -0.011 | no | mirror, +10°, shift (+0,-3) |
| B5-R | 360 | 0.757 | 0.782 | -0.025 | no | mirror, +2.5°, shift (+1,-1) |
| B3-B | 49 | 0.832 | 0.732 | 0.100 | yes | shift (+0,-1) |
| C4-T | 315 | 0.717 | 0.708 | 0.009 | no | mirror, shift (+3,+0) |
| C1 | 332 | 0.672 | 0.689 | -0.017 | no | -2.5°, shift (-1,+5) |
| C2 | 49 | 0.758 | 0.727 | 0.031 | no | +2.5°, shift (-1,+0) |
| C3 | 356 | 0.802 | 0.658 | 0.144 | yes | mirror, shift (+1,-3) |
| C4-M | 242 | 0.890 | 0.648 | 0.242 | yes | shift (+0,-1) |
| C4-B | 81 | 0.866 | 0.756 | 0.111 | yes | +2.5°, shift (-1,+1) |
| C5 | 356 | 0.809 | 0.654 | 0.154 | yes | shift (+0,-1) |
| C6 | 49 | 0.773 | 0.744 | 0.029 | no | mirror, shift (-2,+0) |
| C7 | 340 | 0.691 | 0.693 | -0.003 | no | mirror, +2.5°, shift (+2,+3) |
| D3 | 290 | 0.939 | 0.794 | 0.145 | yes | shift (-1,+1) |
| D4-T | 113 | 0.956 | 0.777 | 0.179 | yes | shift (+0,+1) |
| D5 | 308 | 0.933 | 0.792 | 0.141 | yes | shift (-1,-1) |
| D1 | 95 | 0.721 | 0.694 | 0.026 | no | -5°, shift (+2,+3) |
| D4-B | 350 | 0.782 | 0.726 | 0.056 | no | mirror, shift (-3,-2) |
| D7 | 95 | 0.725 | 0.685 | 0.041 | no | mirror, -7.5°, shift (+3,+4) |
| E3 | 332 | 0.874 | 0.872 | 0.002 | yes | -10°, shift (-1,-1) |
| E4-B | 107 | 0.763 | 0.675 | 0.088 | no | +2.5° |
| E4-T | 205 | 0.869 | 0.708 | 0.161 | yes | shift (+0,+1) |
| E5 | 330 | 0.874 | 0.871 | 0.003 | yes | +10°, shift (+0,-1) |

One-to-one assignment (each region used at most once; the headline):

| map label | tile (region id) | shape IoU | matched |
|---|---|---|---|
| A2 | 73 | 0.715 | no |
| A3 | 134 | 0.692 | no |
| A4 | 346 | 0.674 | no |
| B1 | 350 | 0.714 | no |
| B2 | 352 | 0.693 | no |
| B3-T | 315 | 0.647 | no |
| B4-L | 126 | 0.686 | no |
| B4-R | 347 | 0.666 | no |
| B5-L | 10 | 0.790 | no |
| B5-R | 360 | 0.757 | no |
| B3-B | 49 | 0.832 | yes |
| C4-T | 245 | 0.708 | no |
| C1 | 357 | 0.669 | no |
| C2 | 76 | 0.727 | no |
| C3 | 359 | 0.658 | no |
| C4-M | 242 | 0.890 | yes |
| C4-B | 81 | 0.866 | yes |
| C5 | 356 | 0.809 | yes |
| C6 | 28 | 0.719 | no |
| C7 | 340 | 0.691 | no |
| D3 | 290 | 0.939 | yes |
| D4-T | 113 | 0.956 | yes |
| D5 | 308 | 0.933 | yes |
| D1 | 95 | 0.721 | no |
| D4-B | 239 | 0.726 | no |
| D7 | 252 | 0.668 | no |
| E3 | 332 | 0.874 | yes |
| E4-B | 107 | 0.763 | no |
| E4-T | 205 | 0.869 | yes |
| E5 | 330 | 0.874 | yes |

Best level per piece (highest IoU any region reaches, over the evaluated levels):

| map label | level | region id | shape IoU | margin | reaches threshold |
|---|---|---|---|---|---|
| A2 | 7 | 73 | 0.715 | 0.051 | no |
| A3 | 0 | 181 | 0.787 | 0.031 | no |
| A4 | 0 | 163 | 0.765 | 0.004 | no |
| B1 | 3 | 294 | 0.855 | 0.046 | yes |
| B2 | 3 | 294 | 0.854 | 0.033 | yes |
| B3-T | 0 | 215 | 0.818 | 0.118 | yes |
| B4-L | 14 | 407 | 0.772 | 0.066 | no |
| B4-R | 0 | 27 | 0.802 | 0.032 | yes |
| B5-L | 8 | 10 | 0.790 | 0.024 | no |
| B5-R | 5 | 10 | 0.782 | 0.002 | no |
| B3-B | 0 | 49 | 0.832 | 0.021 | yes |
| C4-T | 0 | 147 | 0.764 | 0.061 | no |
| C1 | 10 | 382 | 0.701 | 0.012 | no |
| C2 | 0 | 49 | 0.758 | 0.023 | no |
| C3 | 0 | 70 | 0.905 | 0.052 | yes |
| C4-M | 1 | 242 | 0.890 | 0.230 | yes |
| C4-B | 0 | 81 | 0.866 | 0.062 | yes |
| C5 | 0 | 69 | 0.896 | 0.017 | yes |
| C6 | 0 | 49 | 0.773 | 0.023 | no |
| C7 | 0 | 200 | 0.709 | -0.019 | no |
| D3 | 3 | 290 | 0.939 | 0.140 | yes |
| D4-T | 0 | 113 | 0.956 | 0.146 | yes |
| D5 | 2 | 253 | 0.942 | 0.147 | yes |
| D1 | 0 | 77 | 0.744 | 0.004 | no |
| D4-B | 7 | 350 | 0.782 | 0.056 | no |
| D7 | 0 | 77 | 0.730 | 0.002 | no |
| E3 | 5 | 332 | 0.874 | 0.002 | yes |
| E4-B | 0 | 107 | 0.763 | 0.040 | no |
| E4-T | 0 | 205 | 0.869 | 0.158 | yes |
| E5 | 5 | 330 | 0.874 | 0.003 | yes |

Per level (one-to-one at the threshold; present = pieces whose best region reaches it):

| level | regions | 1:1 matched | 1:1 fraction | present | mean IoU | baseline | seconds |
|---|---|---|---|---|---|---|---|
| 0 | 215 | 15 | 50.0 % | 15 | 0.808 | 0.499 | 1.60 |
| 1 | 183 | 14 | 46.7 % | 15 | 0.808 | 0.485 | 1.30 |
| 2 | 155 | 14 | 46.7 % | 14 | 0.805 | 0.485 | 1.01 |
| 3 | 132 | 14 | 46.7 % | 14 | 0.807 | 0.481 | 1.20 |
| 4 | 112 | 14 | 46.7 % | 14 | 0.803 | 0.484 | 0.95 |
| 5 | 95 | 13 | 43.3 % | 12 | 0.797 | 0.480 | 0.77 |
| 6 | 81 | 13 | 43.3 % | 12 | 0.792 | 0.474 | 1.08 |
| 7 | 68 | 10 | 33.3 % | 11 | 0.785 | 0.471 | 0.77 |
| 8 | 58 | 5 | 16.7 % | 6 | 0.763 | 0.473 | 0.53 |
| 9 | 49 | 4 | 13.3 % | 4 | 0.745 | 0.470 | 0.55 |
| 10 | 42 | 2 | 6.7 % | 3 | 0.733 | 0.474 | 0.64 |
| 11 | 36 | 3 | 10.0 % | 6 | 0.733 | 0.483 | 0.55 |
| 12 | 30 | 4 | 13.3 % | 5 | 0.714 | 0.488 | 0.56 |
| 13 | 26 | 2 | 6.7 % | 3 | 0.693 | 0.491 | 0.54 |
| 14 | 22 | 2 | 6.7 % | 3 | 0.687 | 0.488 | 0.57 |
| 15 | 18 | 1 | 3.3 % | 3 | 0.684 | 0.489 | 0.67 |
| 16 | 16 | 1 | 3.3 % | 3 | 0.681 | 0.480 | 0.46 |
| 17 | 13 | 1 | 3.3 % | 3 | 0.687 | 0.478 | 0.45 |
| 18 | 11 | 1 | 3.3 % | 2 | 0.683 | 0.466 | 0.48 |
| 19 | 10 | 1 | 3.3 % | 2 | 0.683 | 0.485 | 0.46 |
| 20 | 8 | 1 | 3.3 % | 2 | 0.681 | 0.467 | 0.80 |
| 21 | 7 | 0 | 0.0 % | 0 | 0.660 | 0.437 | 0.48 |
| 22 | 6 | 0 | 0.0 % | 0 | 0.662 | 0.494 | 0.46 |
| 23 | 5 | 0 | 0.0 % | 0 | 0.607 | 0.485 | 0.44 |

Misses (one-to-one): A2 (r73 @ 0.715), A3 (r134 @ 0.692), A4 (r346 @ 0.674), B1 (r350 @ 0.714), B2 (r352 @ 0.693), B3-T (r315 @ 0.647), B4-L (r126 @ 0.686), B4-R (r347 @ 0.666), B5-L (r10 @ 0.790), B5-R (r360 @ 0.757), C4-T (r245 @ 0.708), C1 (r357 @ 0.669), C2 (r76 @ 0.727), C3 (r359 @ 0.658), C6 (r28 @ 0.719), C7 (r340 @ 0.691), D1 (r95 @ 0.721), D4-B (r239 @ 0.726), D7 (r252 @ 0.668), E4-B (r107 @ 0.763)
Misses (greedy): A2 (best r73 @ 0.715), A3 (best r134 @ 0.692), A4 (best r73 @ 0.714), B1 (best r330 @ 0.791), B2 (best r330 @ 0.781), B3-T (best r315 @ 0.647), B4-L (best r126 @ 0.686), B4-R (best r81 @ 0.741), B5-L (best r330 @ 0.778), B5-R (best r360 @ 0.757), C4-T (best r315 @ 0.717), C1 (best r332 @ 0.672), C2 (best r49 @ 0.758), C6 (best r49 @ 0.773), C7 (best r340 @ 0.691), D1 (best r95 @ 0.721), D4-B (best r350 @ 0.782), D7 (best r95 @ 0.725), E4-B (best r107 @ 0.763)

## 7. Non-destructive render modes (`non_destructive`) - PASS

3 tiles x 4 render modes: stored PNG sha256 unchanged, no files added

- render modes: original, greyscale, outline, remap

| tile | sha256 before | sha256 after | api before == after | identical |
|---|---|---|---|---|
| IMG_2295-68adae2e-L07-r332 | 7ebe2d2e2b0c79e3... | 7ebe2d2e2b0c79e3... | yes | yes |
| IMG_2295-68adae2e-L07-r330 | cc94b7f3c4bac7ad... | cc94b7f3c4bac7ad... | yes | yes |
| IMG_2295-68adae2e-L07-r269 | 6f08979b29e54aa4... | 6f08979b29e54aa4... | yes | yes |
- files in tiles/ unchanged: yes

## 8. Provenance immutable (`provenance_immutable`) - PASS

provenance_is_immutable()=True; public members mentioning provenance: ['provenance_is_immutable']; direct UPDATE on 3 rows -> IntegrityError: provenance is immutable

- `provenance_is_immutable()` -> yes
- public Library members mentioning provenance: ['provenance_is_immutable']
- trigger present in sqlite_master: yes (['provenance_immutable'])
- direct `UPDATE provenance` on 3 rows raised IntegrityError: yes (IntegrityError: provenance is immutable)

## UI drive (Playwright) (`ui_drive`) - PASS

5 screenshots; selected='126' twins='127'; saved ['IMG_2295-68adae2e-L07-r126', 'IMG_2295-68adae2e-L07-r127'] with provenance

- url: http://127.0.0.1:35005/
- picked_level: 7
- selected: 126
- twins: 127
- toast_text: Saved 2 tiles to the library
- saved_tile_ids: ['IMG_2295-68adae2e-L07-r126', 'IMG_2295-68adae2e-L07-r127']
- provenance_ok: True
- tapped region 126 (side L, twin 127, iou 0.886) at image px [623, 1649] -> page [146.7, 413.1]

## Per-level counts

| level | merges | regions | on-axis | left | right | pairs | paired | ink fidelity | integrity | band distance | in band | picked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 215 | 15 | 97 | 103 | 47 | 47.0 % | 100.0 % | yes | 16.55 | no |  |
| 1 | 32 | 183 | 15 | 80 | 88 | 44 | 52.4 % | 100.0 % | yes | 12.96 | no |  |
| 2 | 60 | 155 | 15 | 65 | 75 | 41 | 58.6 % | 100.0 % | yes | 9.82 | no |  |
| 3 | 83 | 132 | 15 | 55 | 62 | 40 | 68.4 % | 100.0 % | yes | 7.09 | no |  |
| 4 | 103 | 112 | 15 | 47 | 50 | 37 | 76.3 % | 100.0 % | yes | 4.66 | no |  |
| 5 | 120 | 95 | 14 | 39 | 42 | 32 | 79.0 % | 100.0 % | yes | 2.66 | no |  |
| 6 | 134 | 81 | 14 | 35 | 32 | 28 | 83.6 % | 100.0 % | yes | 1.06 | no |  |
| 7 | 147 | 68 | 13 | 26 | 29 | 23 | 83.6 % | 100.0 % | yes | 0.08 | yes | <-- |
| 8 | 157 | 58 | 11 | 22 | 25 | 21 | 89.4 % | 100.0 % | yes | 0.08 | yes |  |
| 9 | 166 | 49 | 9 | 19 | 21 | 17 | 85.0 % | 100.0 % | yes | 0.33 | no |  |
| 10 | 173 | 42 | 9 | 17 | 16 | 13 | 78.8 % | 100.0 % | yes | 0.89 | no |  |
| 11 | 179 | 36 | 9 | 14 | 13 | 12 | 88.9 % | 100.0 % | yes | 1.37 | no |  |
| 12 | 185 | 30 | 7 | 11 | 12 | 11 | 95.7 % | 100.0 % | yes | 2.02 | no |  |
| 13 | 189 | 26 | 4 | 10 | 12 | 10 | 90.9 % | 100.0 % | yes | 2.59 | no |  |
| 14 | 193 | 22 | 4 | 8 | 10 | 8 | 88.9 % | 100.0 % | yes | 2.91 | no |  |
| 15 | 197 | 18 | 4 | 5 | 9 | 4 | 57.1 % | 100.0 % | yes | 3.23 | no |  |
| 16 | 199 | 16 | 2 | 5 | 9 | 4 | 57.1 % | 100.0 % | yes | 3.55 | no |  |
| 17 | 202 | 13 | 2 | 5 | 6 | 5 | 90.9 % | 100.0 % | yes | 3.79 | no |  |
| 18 | 204 | 11 | 2 | 3 | 6 | 3 | 66.7 % | 100.0 % | yes | 3.95 | no |  |
| 19 | 205 | 10 | 2 | 3 | 5 | 2 | 50.0 % | 100.0 % | yes | 4.03 | no |  |
| 20 | 207 | 8 | 3 | 2 | 3 | 1 | 40.0 % | 100.0 % | yes | 4.11 | no |  |
| 21 | 208 | 7 | 2 | 2 | 3 | 1 | 40.0 % | 100.0 % | yes | 4.27 | no |  |
| 22 | 209 | 6 | 1 | 2 | 3 | 1 | 40.0 % | 100.0 % | yes | 4.44 | no |  |
| 23 | 210 | 5 | 2 | 1 | 2 | 0 | 0.0 % | 100.0 % | yes | 4.43 | no |  |

## Files

- out_dir: `verify_out`
- session_dir: `verify_out/session`
- library_dir: `verify_out/library`
- saved_tile_ids: `['IMG_2295-68adae2e-L07-r332', 'IMG_2295-68adae2e-L07-r330', 'IMG_2295-68adae2e-L07-r269']`
- contact_sheet: `verify_out/contact_sheet.png`
- gt_matches: `verify_out/gt_matches.png`

### UI screenshots

- 01_initial: `verify_out/ui/01_initial.png`
- 02_slider: `verify_out/ui/02_slider.png`
- 03_tap: `verify_out/ui/03_tap.png`
- 04_save: `verify_out/ui/04_save.png`
- 05_tray: `verify_out/ui/05_tray.png`

## Where the drawing disagrees with the thresholds

### 6. Ground truth

- threshold: `{'min_one_to_one_fraction': 0.5, 'per_piece_iou': 0.8}`
- measured: `{'one_to_one_fraction': 0.3333333333333333, 'greedy_fraction': 0.36666666666666664, 'n_never_matched': 15, 'best_level': 0, 'best_level_one_to_one_fraction': 0.5}`
- map IMG_2298.png: one-to-one 10/30 pieces matched at level 7 (33%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (215 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (215 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (68 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B5-L, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.72 at level 7, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 14, B5-L 0.79 at level 8, B5-R 0.78 at level 5, C4-T 0.76 at level 0, C1 0.70 at level 10, C2 0.76 at level 0, C6 0.77 at level 0, C7 0.71 at level 0, D1 0.74 at level 0, D4-B 0.78 at level 7, D7 0.73 at level 0, E4-B 0.76 at level 0.


## Timings

| stage | seconds |
|---|---|
| pipeline | 4.36 |
| library_open | 0.01 |
| check_1_partition_integrity | 24.78 |
| check_4_granularity | 24.33 |
| check_2_ink_fidelity | 0.55 |
| check_3_axis | 0.02 |
| check_5_pairing | 0.00 |
| check_6_ground_truth | 17.46 |
| save_tiles | 0.13 |
| check_7_non_destructive | 0.08 |
| check_8_provenance | 0.00 |
| contact_sheet | 0.62 |
| gt_match_sheet | 0.22 |
| ui_drive | 18.22 |
