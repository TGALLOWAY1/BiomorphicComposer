# Grouping cost - IMG_2295.jpeg - session 4b6d8890

How many gestures the review UI, as built, needs to assemble each chunk of the artist's map. A *tap* selects a region, *merge* is the Merge button, a *level change* is one slider move. The single-level number stays on one slider position; the mixed plan taps the largest clean regions at a coarse level, merges, then descends for the rest.

## Summary

- 30 chunks on the map, 213 atomic leaves in the drawing; map to drawing scale 3.0288 working px per map px (102/155 confident matches agree).
- Placement confident (leaf IoU >= 0.70 and area ratio in 0.6-1.6) for 17/30 (28 placed); uncertain: A2, A3, A4, B3-T, B5-L, B5-R, C7, D4-B, E3, E4-B, E5. Unplaced: D1, D7.
- Confident placements (17 chunks): size 1-15 leaves, median 4 (histogram: 1: 5, 2: 3, 4: 4, 5: 1, 7: 1, 9: 1, 11: 1, 15: 1); 8/17 are one region at some slider level (one tap).
  Single level: 64 taps, 73 gestures in all (median 3, max 16). Mixed levels: 94 gestures (median 4, max 22); 93 when a chunk's mirror twin rides along for free (2 twin pairs found: C3/C5, E3/E5).
- All placements (28 chunks): size 1-15 leaves, median 4 (histogram: 1: 6, 2: 6, 4: 7, 5: 2, 6: 1, 7: 1, 9: 1, 11: 2, 13: 1, 15: 1); 11/28 are one region at some slider level (one tap).
  Single level: 105 taps, 122 gestures in all (median 3, max 16). Mixed levels: 172 gestures (median 4, max 24); 170 when a chunk's mirror twin rides along for free (2 twin pairs found: C3/C5, E3/E5).
- Chunks the atomic split cannot reproduce (leaf IoU < 0.80): A2, A3, A4, B1, B2, B3-T, B4-L, B4-R, B5-L, B5-R, C2, C6, C7, D4-B, E4-B.

![grouping sheet](grouping_sheet.jpg)

## Per chunk

| chunk | placed | leaves | leaf IoU | area ratio | one region at | best single level | taps | merge | mixed gestures | mixed steps |
|---|---|---|---|---|---|---|---|---|---|---|
| A2 | ? | 5 | 0.67 | 0.53 | none | 1 (181 parts) | 4 | yes | 8 | L8:1 > L3:1 > L1:2 |
| A3 | ? | 11 | 0.43 | 0.76 | none | 0 (213 parts) | 11 | yes | 24 | L8:1 > L6:1 > L5:1 > L4:1 > L3:1 > L2:1 > L1:2 > L0:2 |
| A4 | ? | 2 | 0.64 | 0.41 | none | 1 (181 parts) | 2 | yes | 3 | L1:2 |
| B1 | ok | 9 | 0.78 | 0.92 | none | 0 (213 parts) | 9 | yes | 7 | L17:1 > L3:1 > L0:1 |
| B2 | ok | 11 | 0.79 | 0.95 | none | 0 (213 parts) | 11 | yes | 22 | L10:1 > L8:1 > L4:4 > L3:1 > L2:1 > L1:1 > L0:1 |
| B3-B | ok | 2 | 0.83 | 1.16 | 1-12 | 12 (30 parts) | 1 | - | 1 | L12:1 |
| B3-T | ? | 6 | 0.67 | 1.00 | none | 5 (95 parts) | 3 | yes | 6 | L7:2 > L5:1 |
| B4-L | ok | 1 | 0.70 | 0.66 | 0-10 | 10 (43 parts) | 1 | - | 1 | L10:1 |
| B4-R | ok | 2 | 0.72 | 1.33 | none | 5 (95 parts) | 2 | yes | 4 | L6:1 > L5:1 |
| B5-L | ? | 1 | 0.79 | 1.74 | 0-1 | 1 (181 parts) | 1 | - | 1 | L1:1 |
| B5-R | ? | 2 | 0.77 | 0.50 | none | 1 (181 parts) | 2 | yes | 4 | L6:1 > L1:1 |
| C1 | ok | 15 | 0.86 | 0.95 | none | 0 (213 parts) | 15 | yes | 20 | L11:2 > L4:1 > L3:3 > L2:1 > L1:1 > L0:1 |
| C2 | ok | 5 | 0.72 | 0.83 | none | 0 (213 parts) | 5 | yes | 8 | L14:1 > L5:2 > L0:1 |
| C3 | ok | 1 | 0.90 | 1.02 | 0-5 | 5 (95 parts) | 1 | - | 1 | L5:1 |
| C4-B | ok | 1 | 0.86 | 0.94 | 0-8 | 8 (59 parts) | 1 | - | 1 | L8:1 |
| C4-M | ok | 2 | 0.90 | 1.00 | 1-8 | 8 (59 parts) | 1 | - | 1 | L8:1 |
| C4-T | ok | 4 | 0.80 | 0.97 | none | 7 (69 parts) | 2 | yes | 4 | L12:1 > L7:1 |
| C5 | ok | 1 | 0.90 | 1.02 | 0-5 | 5 (95 parts) | 1 | - | 1 | L5:1 |
| C6 | ok | 4 | 0.72 | 0.85 | none | 1 (181 parts) | 4 | yes | 7 | L14:1 > L5:1 > L1:1 |
| C7 | ? | 4 | 0.59 | 0.56 | none | 0 (213 parts) | 4 | yes | 7 | L3:1 > L1:1 > L0:1 |
| D3 | ok | 4 | 0.92 | 0.93 | none | 0 (213 parts) | 4 | yes | 6 | L2:1 > L0:3 |
| D4-B | ? | 13 | 0.63 | 0.51 | none | 2 (154 parts) | 10 | yes | 19 | L11:1 > L7:1 > L5:2 > L4:1 > L3:2 > L2:2 |
| D4-T | ok | 4 | 0.95 | 0.91 | none | 0 (213 parts) | 4 | yes | 8 | L8:1 > L2:1 > L0:2 |
| D5 | ok | 7 | 0.94 | 0.96 | 3-7 | 7 (69 parts) | 1 | - | 1 | L7:1 |
| E3 | ? | 4 | 0.89 | 0.52 | 6 | 6 (81 parts) | 1 | - | 1 | L6:1 |
| E4-B | ? | 2 | 0.63 | 0.84 | none | 0 (213 parts) | 2 | yes | 4 | L1:1 > L0:1 |
| E4-T | ok | 1 | 0.87 | 0.82 | 0-6 | 6 (81 parts) | 1 | - | 1 | L6:1 |
| E5 | ? | 4 | 0.87 | 0.50 | 6 | 6 (81 parts) | 1 | - | 1 | L6:1 |

## Reading it

- *placed*: `ok` when the chunk's location on the drawing is trusted, `?` when it is not (judge the sheet); `twin` when it was placed as the mirror twin of another chunk that had taken the same leaves.
- *leaf IoU* is the shape IoU between the union of the chunk's leaves and the map piece (mirror allowed). Below the threshold, no grouping gesture reaches the artist's shape: a leaf straddles the chunk's edge. *area ratio* is the union's area over the piece's area at the map-to-drawing scale (1 = same size).
- *one region at*: slider levels where the chunk is exactly one region. Empty means the merge tree never forms it: the chunk's cells join neighbours before each other.
- *best single level*: the slider position that assembles the chunk with the fewest gestures without moving the slider again. Level 0 (every cell its own part) always can.
