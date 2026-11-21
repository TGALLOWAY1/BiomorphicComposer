# Biomorphic Composer - verification report

Generated 2026-11-20T09:45:15Z. Photo `samples/IMG_2295.jpeg`, map `samples/IMG_2298.png`, session `verify_out/session` (id 9c70723a), pipeline version 0.2.0.

## Summary: FAIL: 9 passed; 1 failed (ground_truth)

| check | name | status | summary |
|---|---|---|---|
| 0. Pipeline run | `pipeline` | PASS | pipeline wrote verify_out/session |
| 1. Partition integrity | `partition_integrity` | PASS | 24 levels: label map == figure mask at every level, tiles re-composite pixel-exactly |
| 2. Ink fidelity | `ink_fidelity` | PASS | level 7: 100.0% of 61994 boundary px on ink (dilated 1 px); threshold 90%; over all levels min 100.0% / max 100.0% |
| 3. Axis | `axis_check` | PASS | axis x=812.5 is -0.5 px (-0.0% of width 1484) from the figure centre 813.0; allowed +/-15%; score=0.975 mirror_iou=0.975 (recomputed 0.975) |
| 4. Granularity | `granularity` | PASS | picked level 7: 69 regions (13 on-axis, 28 L, 28 R); levels in band [7, 8] |
| 5. Pairing | `pairing_check` | PASS | level 7: 54/56 off-axis regions have a twin (96.4%; threshold 80%); 2 unpaired |
| 6. Ground truth | `ground_truth` | FAIL | map IMG_2298.png: one-to-one 9/30 pieces matched at level 7 (30%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (213 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (213 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (69 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.76 at level 0, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 15, B4-R 0.79 at level 0, B5-R 0.78 at level 6, C4-T 0.75 at level 0, C1 0.70 at level 8, C2 0.77 at level 0, C6 0.78 at level 0, C7 0.71 at level 8, D1 0.75 at level 0, D4-B 0.77 at level 0, D7 0.73 at level 0, E4-B 0.76 at level 0. |
| 7. Non-destructive render modes | `non_destructive` | PASS | 3 tiles x 4 render modes: stored PNG sha256 unchanged, no files added |
| 8. Provenance immutable | `provenance_immutable` | PASS | provenance_is_immutable()=True; public members mentioning provenance: ['provenance_is_immutable']; direct UPDATE on 3 rows -> IntegrityError: provenance is immutable |
| UI drive (Playwright) | `ui_drive` | PASS | 5 screenshots; selected='51' twins='46'; saved ['IMG_2295-9c70723a-L07-r46', 'IMG_2295-9c70723a-L07-r51'] with provenance |

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
| 0 | 213 | yes | 0 | yes | 0 | 0 | 0 |
| 1 | 181 | yes | 0 | yes | 0 | 0 | 0 |
| 2 | 154 | yes | 0 | yes | 0 | 0 | 0 |
| 3 | 131 | yes | 0 | yes | 0 | 0 | 0 |
| 4 | 111 | yes | 0 | yes | 0 | 0 | 0 |
| 5 | 95 | yes | 0 | yes | 0 | 0 | 0 |
| 6 | 81 | yes | 0 | yes | 0 | 0 | 0 |
| 7 | 69 | yes | 0 | yes | 0 | 0 | 0 |
| 8 | 59 | yes | 0 | yes | 0 | 0 | 0 |
| 9 | 49 | yes | 0 | yes | 0 | 0 | 0 |
| 10 | 43 | yes | 0 | yes | 0 | 0 | 0 |
| 11 | 36 | yes | 0 | yes | 0 | 0 | 0 |
| 12 | 30 | yes | 0 | yes | 0 | 0 | 0 |
| 13 | 26 | yes | 0 | yes | 0 | 0 | 0 |
| 14 | 23 | yes | 0 | yes | 0 | 0 | 0 |
| 15 | 19 | yes | 0 | yes | 0 | 0 | 0 |
| 16 | 17 | yes | 0 | yes | 0 | 0 | 0 |
| 17 | 13 | yes | 0 | yes | 0 | 0 | 0 |
| 18 | 11 | yes | 0 | yes | 0 | 0 | 0 |
| 19 | 10 | yes | 0 | yes | 0 | 0 | 0 |
| 20 | 9 | yes | 0 | yes | 0 | 0 | 0 |
| 21 | 7 | yes | 0 | yes | 0 | 0 | 0 |
| 22 | 6 | yes | 0 | yes | 0 | 0 | 0 |
| 23 | 5 | yes | 0 | yes | 0 | 0 | 0 |

## 2. Ink fidelity (`ink_fidelity`) - PASS

level 7: 100.0% of 61994 boundary px on ink (dilated 1 px); threshold 90%; over all levels min 100.0% / max 100.0%

Ink mask dilated by 1 px; picked level 7.

| level | boundary px | on ink px | off ink px | fraction on ink |
|---|---|---|---|---|
| 0 | 76100 | 76078 | 22 | 100.0 % |
| 1 | 74279 | 74258 | 21 | 100.0 % |
| 2 | 72774 | 72755 | 19 | 100.0 % |
| 3 | 71082 | 71070 | 12 | 100.0 % |
| 4 | 69898 | 69894 | 4 | 100.0 % |
| 5 | 67398 | 67397 | 1 | 100.0 % |
| 6 | 65181 | 65180 | 1 | 100.0 % |
| 7 | 61994 | 61993 | 1 | 100.0 % |
| 8 | 57706 | 57705 | 1 | 100.0 % |
| 9 | 53570 | 53569 | 1 | 100.0 % |
| 10 | 51519 | 51518 | 1 | 100.0 % |
| 11 | 47600 | 47599 | 1 | 100.0 % |
| 12 | 43441 | 43440 | 1 | 100.0 % |
| 13 | 40618 | 40617 | 1 | 100.0 % |
| 14 | 37491 | 37490 | 1 | 100.0 % |
| 15 | 33005 | 33004 | 1 | 100.0 % |
| 16 | 32021 | 32020 | 1 | 100.0 % |
| 17 | 28810 | 28809 | 1 | 100.0 % |
| 18 | 27530 | 27529 | 1 | 100.0 % |
| 19 | 25945 | 25944 | 1 | 100.0 % |
| 20 | 24698 | 24697 | 1 | 100.0 % |
| 21 | 22290 | 22289 | 1 | 100.0 % |
| 22 | 17822 | 17821 | 1 | 100.0 % |
| 23 | 15585 | 15584 | 1 | 100.0 % |

## 3. Axis (`axis_check`) - PASS

axis x=812.5 is -0.5 px (-0.0% of width 1484) from the figure centre 813.0; allowed +/-15%; score=0.975 mirror_iou=0.975 (recomputed 0.975)

- axis x = 812.5 px, angle = 0.00 deg
- figure box (working px): x=71 y=70 w=1484 h=2338, centre x = 813.0
- offset from centre: -0.5 px = -0.0 % of the figure width (allowed +/-15.0 %)
- pipeline axis.score = 0.975, axis.mirror_iou = 0.975, mirror IoU recomputed by verify = 0.975

## 4. Granularity (`granularity`) - PASS

picked level 7: 69 regions (13 on-axis, 28 L, 28 R); levels in band [7, 8]

- target band: 50-75 regions, about 12 on-axis, at most 29 per side
- picked level: **7**; levels inside the band: [7, 8]
- the per-level table is below.

## 5. Pairing (`pairing_check`) - PASS

level 7: 54/56 off-axis regions have a twin (96.4%; threshold 80%); 2 unpaired

- level 7: 54 of 56 off-axis regions paired (96.4 %), 13 on-axis regions

Unpaired regions (2), largest first:

| region | side | area px | bbox x,y,w,h |
|---|---|---|---|
| 344 | R | 22649 | 818,70,737,2105 |
| 343 | L | 15853 | 134,81,684,1305 |

Top pairs by IoU (12 of 27):

| a | b | iou | dx | dy | delta E |
|---|---|---|---|---|---|
| 352 | 353 | 0.949 | -1.0 | -2.0 | 1.6 |
| 51 | 46 | 0.947 | 0.0 | 1.0 | 1.8 |
| 339 | 340 | 0.942 | 1.0 | 0.0 | 3.5 |
| 128 | 129 | 0.941 | -1.0 | -3.0 | 2.4 |
| 33 | 31 | 0.939 | 0.0 | 0.0 | 0.9 |
| 318 | 319 | 0.938 | 1.0 | 2.0 | 0.9 |
| 330 | 331 | 0.932 | -1.0 | -1.0 | 3.2 |
| 325 | 296 | 0.930 | 1.0 | -1.0 | 0.3 |
| 278 | 293 | 0.928 | -1.0 | 1.0 | 5.3 |
| 78 | 76 | 0.928 | -1.0 | -2.0 | 3.3 |
| 54 | 52 | 0.918 | 0.0 | -1.0 | 2.4 |
| 284 | 280 | 0.915 | -1.0 | -1.0 | 0.4 |

## 6. Ground truth (`ground_truth`) - FAIL

map IMG_2298.png: one-to-one 9/30 pieces matched at level 7 (30%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (213 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (213 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (69 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.76 at level 0, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 15, B4-R 0.79 at level 0, B5-R 0.78 at level 6, C4-T 0.75 at level 0, C1 0.70 at level 8, C2 0.77 at level 0, C6 0.78 at level 0, C7 0.71 at level 8, D1 0.75 at level 0, D4-B 0.77 at level 0, D7 0.73 at level 0, E4-B 0.76 at level 0.

- map: `samples/IMG_2298.png`; labels: `samples/IMG_2298.labels.json` (loaded)
- 30 map pieces, 69 candidate regions at the picked level 7; a piece matches at scale-normalised shape IoU >= 0.80 (mirror and +/-10 deg allowed)
- labels attached to 30 pieces; label entries with no piece: none; pieces left unlabelled: none
- **one-to-one assignment (headline): 9 of 30 (30.0 %); pass rule >= 50.0 %**
- one-to-one matched fraction if the bar were: 0.60 -> 100%, 0.65 -> 97%, 0.70 -> 80%, 0.75 -> 43%, 0.80 -> 30%, 0.85 -> 17%, 0.90 -> 13%
- greedy best per piece: 11 of 30 (36.7 %); shape IoU min/median/mean/max = 0.651 / 0.758 / 0.776 / 0.955; baseline (median IoU over every piece x region pair) 0.474
- best level over the evaluated levels: 0 (213 regions), one-to-one 15 of 30 (50.0 %)
- pieces that never reach IoU 0.80 at any level: A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B
- why: level 0 (213 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (69 regions), so the picked granularity is coarser than the map's components.
- why: 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.76 at level 0, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 15, B4-R 0.79 at level 0, B5-R 0.78 at level 6, C4-T 0.75 at level 0, C1 0.70 at level 8, C2 0.77 at level 0, C6 0.78 at level 0, C7 0.71 at level 8, D1 0.75 at level 0, D4-B 0.77 at level 0, D7 0.73 at level 0, E4-B 0.76 at level 0.
- regions that are the greedy best of several pieces at once: r78 <- A2, A4; r110 <- A3, E4-B, E4-T; r84 <- B2, B4-R, C4-B, E3, E5; r309 <- B3-T, C4-T; r251 <- B5-L, B5-R; r245 <- B3-B, C2, C6; r82 <- D1, D7

Greedy best per piece (margin = IoU minus the runner-up region's IoU):

| map label | best tile (region id) | shape IoU | runner-up | margin | matched | transform |
|---|---|---|---|---|---|---|
| A2 | 78 | 0.711 | 0.715 | -0.004 | no | shift (+6,+2) |
| A3 | 110 | 0.705 | 0.704 | 0.002 | no | +2.5°, shift (+2,-2) |
| A4 | 78 | 0.713 | 0.715 | -0.002 | no | mirror, -2.5°, shift (+5,-1) |
| B1 | 352 | 0.739 | 0.809 | -0.070 | no | mirror, +5°, shift (+0,-2) |
| B2 | 84 | 0.821 | 0.768 | 0.053 | yes | +10°, shift (+0,+1) |
| B3-T | 309 | 0.651 | 0.657 | -0.007 | no | -2.5°, shift (-2,+0) |
| B4-L | 128 | 0.700 | 0.741 | -0.041 | no | -5°, shift (+0,-6) |
| B4-R | 84 | 0.743 | 0.726 | 0.018 | no | mirror, +5°, shift (+0,+4) |
| B5-L | 251 | 0.787 | 0.757 | 0.030 | no | mirror, -10°, shift (+2,-1) |
| B5-R | 251 | 0.782 | 0.762 | 0.019 | no | -7.5°, shift (+3,-2) |
| B3-B | 245 | 0.833 | 0.717 | 0.116 | yes | shift (+0,-1) |
| C4-T | 309 | 0.713 | 0.707 | 0.007 | no | mirror, shift (+4,+0) |
| C1 | 351 | 0.665 | 0.680 | -0.015 | no | +10°, shift (+6,-1) |
| C2 | 245 | 0.757 | 0.740 | 0.017 | no | +2.5°, shift (-1,+0) |
| C3 | 339 | 0.819 | 0.803 | 0.016 | yes | +2.5°, shift (+0,-1) |
| C4-M | 237 | 0.902 | 0.643 | 0.259 | yes | shift (+0,-1) |
| C4-B | 84 | 0.865 | 0.722 | 0.143 | yes | +2.5°, shift (+0,+1) |
| C5 | 340 | 0.807 | 0.787 | 0.019 | yes | shift (+0,-1) |
| C6 | 245 | 0.771 | 0.752 | 0.019 | no | mirror, shift (-2,+0) |
| C7 | 353 | 0.672 | 0.682 | -0.010 | no | +5°, shift (+0,+6) |
| D3 | 284 | 0.939 | 0.799 | 0.140 | yes | shift (+0,+1) |
| D4-T | 115 | 0.955 | 0.742 | 0.213 | yes | shift (-1,+0) |
| D5 | 280 | 0.937 | 0.790 | 0.147 | yes | shift (-1,-1) |
| D1 | 82 | 0.746 | 0.718 | 0.028 | no | mirror, +7.5°, shift (+0,+3) |
| D4-B | 229 | 0.722 | 0.698 | 0.024 | no | shift (+0,-1) |
| D7 | 82 | 0.731 | 0.722 | 0.009 | no | +7.5°, shift (-1,+4) |
| E3 | 84 | 0.813 | 0.810 | 0.004 | yes | mirror, -10°, shift (+2,+2) |
| E4-B | 110 | 0.760 | 0.673 | 0.087 | no | +2.5° |
| E4-T | 110 | 0.722 | 0.686 | 0.036 | no | mirror, +7.5°, shift (+0,+3) |
| E5 | 84 | 0.814 | 0.804 | 0.010 | yes | -10°, shift (+1,+2) |

One-to-one assignment (each region used at most once; the headline):

| map label | tile (region id) | shape IoU | matched |
|---|---|---|---|
| A2 | 76 | 0.715 | no |
| A3 | 42 | 0.704 | no |
| A4 | 78 | 0.713 | no |
| B1 | 352 | 0.739 | no |
| B2 | 353 | 0.738 | no |
| B3-T | 309 | 0.651 | no |
| B4-L | 128 | 0.700 | no |
| B4-R | 347 | 0.639 | no |
| B5-L | 251 | 0.787 | no |
| B5-R | 349 | 0.754 | no |
| B3-B | 245 | 0.833 | yes |
| C4-T | 235 | 0.707 | no |
| C1 | 350 | 0.664 | no |
| C2 | 82 | 0.740 | no |
| C3 | 339 | 0.819 | yes |
| C4-M | 237 | 0.902 | yes |
| C4-B | 84 | 0.865 | yes |
| C5 | 340 | 0.807 | yes |
| C6 | 79 | 0.741 | no |
| C7 | 351 | 0.680 | no |
| D3 | 284 | 0.939 | yes |
| D4-T | 115 | 0.955 | yes |
| D5 | 280 | 0.937 | yes |
| D1 | 98 | 0.718 | no |
| D4-B | 229 | 0.722 | no |
| D7 | 222 | 0.673 | no |
| E3 | 331 | 0.810 | yes |
| E4-B | 110 | 0.760 | no |
| E4-T | 111 | 0.668 | no |
| E5 | 330 | 0.764 | no |

Best level per piece (highest IoU any region reaches, over the evaluated levels):

| map label | level | region id | shape IoU | margin | reaches threshold |
|---|---|---|---|---|---|
| A2 | 0 | 164 | 0.758 | 0.033 | no |
| A3 | 0 | 182 | 0.787 | 0.031 | no |
| A4 | 0 | 164 | 0.765 | 0.004 | no |
| B1 | 3 | 290 | 0.857 | 0.048 | yes |
| B2 | 3 | 290 | 0.850 | 0.028 | yes |
| B3-T | 0 | 213 | 0.818 | 0.113 | yes |
| B4-L | 15 | 406 | 0.768 | 0.008 | no |
| B4-R | 0 | 29 | 0.793 | 0.023 | no |
| B5-L | 0 | 153 | 0.831 | 0.012 | yes |
| B5-R | 6 | 251 | 0.782 | 0.008 | no |
| B3-B | 1 | 245 | 0.833 | 0.028 | yes |
| C4-T | 0 | 149 | 0.752 | 0.053 | no |
| C1 | 8 | 366 | 0.701 | 0.021 | no |
| C2 | 0 | 49 | 0.773 | 0.033 | no |
| C3 | 0 | 73 | 0.904 | 0.044 | yes |
| C4-M | 1 | 237 | 0.902 | 0.241 | yes |
| C4-B | 0 | 84 | 0.865 | 0.055 | yes |
| C5 | 0 | 72 | 0.904 | 0.034 | yes |
| C6 | 0 | 49 | 0.783 | 0.031 | no |
| C7 | 8 | 366 | 0.708 | 0.026 | no |
| D3 | 3 | 284 | 0.939 | 0.140 | yes |
| D4-T | 0 | 115 | 0.955 | 0.145 | yes |
| D5 | 1 | 242 | 0.947 | 0.205 | yes |
| D1 | 0 | 82 | 0.746 | -0.001 | no |
| D4-B | 0 | 167 | 0.771 | 0.004 | no |
| D7 | 0 | 82 | 0.731 | -0.000 | no |
| E3 | 6 | 332 | 0.885 | 0.013 | yes |
| E4-B | 0 | 110 | 0.760 | 0.037 | no |
| E4-T | 0 | 204 | 0.867 | 0.130 | yes |
| E5 | 6 | 332 | 0.882 | 0.009 | yes |

Per level (one-to-one at the threshold; present = pieces whose best region reaches it):

| level | regions | 1:1 matched | 1:1 fraction | present | mean IoU | baseline | seconds |
|---|---|---|---|---|---|---|---|
| 0 | 213 | 15 | 50.0 % | 15 | 0.811 | 0.496 | 1.30 |
| 1 | 181 | 14 | 46.7 % | 15 | 0.812 | 0.484 | 1.11 |
| 2 | 154 | 14 | 46.7 % | 14 | 0.808 | 0.484 | 1.00 |
| 3 | 131 | 14 | 46.7 % | 13 | 0.805 | 0.478 | 0.89 |
| 4 | 111 | 13 | 43.3 % | 13 | 0.805 | 0.492 | 0.75 |
| 5 | 95 | 11 | 36.7 % | 13 | 0.793 | 0.483 | 0.74 |
| 6 | 81 | 11 | 36.7 % | 11 | 0.786 | 0.481 | 0.66 |
| 7 | 69 | 9 | 30.0 % | 11 | 0.776 | 0.474 | 0.53 |
| 8 | 59 | 5 | 16.7 % | 7 | 0.757 | 0.476 | 0.53 |
| 9 | 49 | 1 | 3.3 % | 1 | 0.725 | 0.474 | 0.51 |
| 10 | 43 | 1 | 3.3 % | 1 | 0.718 | 0.463 | 0.58 |
| 11 | 36 | 3 | 10.0 % | 6 | 0.737 | 0.442 | 0.57 |
| 12 | 30 | 3 | 10.0 % | 6 | 0.731 | 0.455 | 0.49 |
| 13 | 26 | 2 | 6.7 % | 5 | 0.716 | 0.483 | 0.52 |
| 14 | 23 | 2 | 6.7 % | 4 | 0.714 | 0.480 | 0.46 |
| 15 | 19 | 2 | 6.7 % | 4 | 0.710 | 0.481 | 0.41 |
| 16 | 17 | 2 | 6.7 % | 4 | 0.696 | 0.465 | 0.43 |
| 17 | 13 | 2 | 6.7 % | 4 | 0.688 | 0.504 | 0.39 |
| 18 | 11 | 1 | 3.3 % | 2 | 0.674 | 0.474 | 0.42 |
| 19 | 10 | 1 | 3.3 % | 2 | 0.671 | 0.473 | 0.40 |
| 20 | 9 | 1 | 3.3 % | 1 | 0.667 | 0.478 | 0.41 |
| 21 | 7 | 1 | 3.3 % | 1 | 0.643 | 0.440 | 0.43 |
| 22 | 6 | 1 | 3.3 % | 1 | 0.644 | 0.495 | 0.41 |
| 23 | 5 | 1 | 3.3 % | 1 | 0.624 | 0.510 | 0.37 |

Misses (one-to-one): A2 (r76 @ 0.715), A3 (r42 @ 0.704), A4 (r78 @ 0.713), B1 (r352 @ 0.739), B2 (r353 @ 0.738), B3-T (r309 @ 0.651), B4-L (r128 @ 0.700), B4-R (r347 @ 0.639), B5-L (r251 @ 0.787), B5-R (r349 @ 0.754), C4-T (r235 @ 0.707), C1 (r350 @ 0.664), C2 (r82 @ 0.740), C6 (r79 @ 0.741), C7 (r351 @ 0.680), D1 (r98 @ 0.718), D4-B (r229 @ 0.722), D7 (r222 @ 0.673), E4-B (r110 @ 0.760), E4-T (r111 @ 0.668), E5 (r330 @ 0.764)
Misses (greedy): A2 (best r78 @ 0.711), A3 (best r110 @ 0.705), A4 (best r78 @ 0.713), B1 (best r352 @ 0.739), B3-T (best r309 @ 0.651), B4-L (best r128 @ 0.700), B4-R (best r84 @ 0.743), B5-L (best r251 @ 0.787), B5-R (best r251 @ 0.782), C4-T (best r309 @ 0.713), C1 (best r351 @ 0.665), C2 (best r245 @ 0.757), C6 (best r245 @ 0.771), C7 (best r353 @ 0.672), D1 (best r82 @ 0.746), D4-B (best r229 @ 0.722), D7 (best r82 @ 0.731), E4-B (best r110 @ 0.760), E4-T (best r110 @ 0.722)

## 7. Non-destructive render modes (`non_destructive`) - PASS

3 tiles x 4 render modes: stored PNG sha256 unchanged, no files added

- render modes: original, greyscale, outline, remap

| tile | sha256 before | sha256 after | api before == after | identical |
|---|---|---|---|---|
| IMG_2295-9c70723a-L07-r352 | 4493cebb18071c31... | 4493cebb18071c31... | yes | yes |
| IMG_2295-9c70723a-L07-r353 | 78190b1085b8906b... | 78190b1085b8906b... | yes | yes |
| IMG_2295-9c70723a-L07-r248 | 4940b0b3d10fb9a2... | 4940b0b3d10fb9a2... | yes | yes |
- files in tiles/ unchanged: yes

## 8. Provenance immutable (`provenance_immutable`) - PASS

provenance_is_immutable()=True; public members mentioning provenance: ['provenance_is_immutable']; direct UPDATE on 3 rows -> IntegrityError: provenance is immutable

- `provenance_is_immutable()` -> yes
- public Library members mentioning provenance: ['provenance_is_immutable']
- trigger present in sqlite_master: yes (['provenance_immutable'])
- direct `UPDATE provenance` on 3 rows raised IntegrityError: yes (IntegrityError: provenance is immutable)

## UI drive (Playwright) (`ui_drive`) - PASS

5 screenshots; selected='51' twins='46'; saved ['IMG_2295-9c70723a-L07-r46', 'IMG_2295-9c70723a-L07-r51'] with provenance

- url: http://127.0.0.1:42841/#review
- picked_level: 7
- selected: 51
- twins: 46
- toast_text: Saved 2 tiles to the library
- saved_tile_ids: ['IMG_2295-9c70723a-L07-r46', 'IMG_2295-9c70723a-L07-r51']
- provenance_ok: True
- tapped region 51 (side L, twin 46, iou 0.947) at image px [674, 782] -> page [158.2, 356.0]

## Per-level counts

| level | merges | regions | on-axis | left | right | pairs | paired | ink fidelity | integrity | band distance | in band | picked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 213 | 15 | 96 | 102 | 58 | 58.6 % | 100.0 % | yes | 16.32 | no |  |
| 1 | 32 | 181 | 15 | 80 | 86 | 55 | 66.3 % | 100.0 % | yes | 12.66 | no |  |
| 2 | 59 | 154 | 15 | 67 | 72 | 50 | 71.9 % | 100.0 % | yes | 9.54 | no |  |
| 3 | 82 | 131 | 15 | 57 | 59 | 45 | 77.6 % | 100.0 % | yes | 6.80 | no |  |
| 4 | 102 | 111 | 15 | 47 | 49 | 42 | 87.5 % | 100.0 % | yes | 4.51 | no |  |
| 5 | 118 | 95 | 14 | 40 | 41 | 36 | 88.9 % | 100.0 % | yes | 2.59 | no |  |
| 6 | 132 | 81 | 14 | 34 | 33 | 32 | 95.5 % | 100.0 % | yes | 0.99 | no |  |
| 7 | 144 | 69 | 13 | 28 | 28 | 27 | 96.4 % | 100.0 % | yes | 0.08 | yes | <-- |
| 8 | 154 | 59 | 13 | 23 | 23 | 23 | 100.0 % | 100.0 % | yes | 0.08 | yes |  |
| 9 | 164 | 49 | 11 | 19 | 19 | 19 | 100.0 % | 100.0 % | yes | 0.16 | no |  |
| 10 | 170 | 43 | 11 | 16 | 16 | 16 | 100.0 % | 100.0 % | yes | 0.64 | no |  |
| 11 | 177 | 36 | 10 | 13 | 13 | 13 | 100.0 % | 100.0 % | yes | 1.29 | no |  |
| 12 | 183 | 30 | 10 | 10 | 10 | 10 | 100.0 % | 100.0 % | yes | 1.77 | no |  |
| 13 | 187 | 26 | 8 | 9 | 9 | 9 | 100.0 % | 100.0 % | yes | 2.25 | no |  |
| 14 | 190 | 23 | 7 | 8 | 8 | 8 | 100.0 % | 100.0 % | yes | 2.58 | no |  |
| 15 | 194 | 19 | 7 | 6 | 6 | 6 | 100.0 % | 100.0 % | yes | 2.90 | no |  |
| 16 | 196 | 17 | 7 | 5 | 5 | 5 | 100.0 % | 100.0 % | yes | 3.06 | no |  |
| 17 | 200 | 13 | 7 | 3 | 3 | 3 | 100.0 % | 100.0 % | yes | 3.38 | no |  |
| 18 | 202 | 11 | 7 | 2 | 2 | 2 | 100.0 % | 100.0 % | yes | 3.54 | no |  |
| 19 | 203 | 10 | 6 | 2 | 2 | 2 | 100.0 % | 100.0 % | yes | 3.70 | no |  |
| 20 | 204 | 9 | 5 | 2 | 2 | 2 | 100.0 % | 100.0 % | yes | 3.86 | no |  |
| 21 | 206 | 7 | 5 | 1 | 1 | 1 | 100.0 % | 100.0 % | yes | 4.02 | no |  |
| 22 | 207 | 6 | 4 | 1 | 1 | 1 | 100.0 % | 100.0 % | yes | 4.19 | no |  |
| 23 | 208 | 5 | 3 | 1 | 1 | 1 | 100.0 % | 100.0 % | yes | 4.35 | no |  |

## Files

- out_dir: `verify_out`
- session_dir: `verify_out/session`
- library_dir: `verify_out/library`
- saved_tile_ids: `['IMG_2295-9c70723a-L07-r352', 'IMG_2295-9c70723a-L07-r353', 'IMG_2295-9c70723a-L07-r248']`
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
- measured: `{'one_to_one_fraction': 0.3, 'greedy_fraction': 0.36666666666666664, 'n_never_matched': 15, 'best_level': 0, 'best_level_one_to_one_fraction': 0.5}`
- map IMG_2298.png: one-to-one 9/30 pieces matched at level 7 (30%; pass >= 50% at shape IoU >= 0.80); greedy best 11/30; best level 0 (213 regions) one-to-one 15/30; never >= 0.80 at any level: A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B; why: level 0 (213 regions) reproduces more of the artist's components one-to-one (15/30) than the picked level 7 (69 regions), so the picked granularity is coarser than the map's components. 15 piece(s) never reach IoU 0.80 at any level (A2, A3, A4, B4-L, B4-R, B5-R, C4-T, C1, C2, C6, C7, D1, D4-B, D7, E4-B): no region at any level has their shape, i.e. the merge tree cuts them differently from the artist (joined to a neighbour or split along internal ink); 15 of them come within 0.1 of the threshold: A2 0.76 at level 0, A3 0.79 at level 0, A4 0.77 at level 0, B4-L 0.77 at level 15, B4-R 0.79 at level 0, B5-R 0.78 at level 6, C4-T 0.75 at level 0, C1 0.70 at level 8, C2 0.77 at level 0, C6 0.78 at level 0, C7 0.71 at level 8, D1 0.75 at level 0, D4-B 0.77 at level 0, D7 0.73 at level 0, E4-B 0.76 at level 0.


## Timings

| stage | seconds |
|---|---|
| pipeline | 11.23 |
| library_open | 0.03 |
| check_1_partition_integrity | 18.37 |
| check_4_granularity | 21.41 |
| check_2_ink_fidelity | 0.36 |
| check_3_axis | 0.02 |
| check_5_pairing | 0.00 |
| check_6_ground_truth | 14.48 |
| save_tiles | 0.22 |
| check_7_non_destructive | 0.10 |
| check_8_provenance | 0.00 |
| contact_sheet | 0.59 |
| gt_match_sheet | 0.24 |
| ui_drive | 14.17 |
