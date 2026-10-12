# Samples

- `IMG_2295.jpeg` — the source drawing, a phone photo. This copy is 1932 × 2576 px, already portrait (EXIF orientation 1); the artist's original is 3024 × 4032 px and may be stored sideways with an EXIF orientation tag, which `biomorphic ingest` corrects.
- `IMG_2298.png` — the artist's hand-made segmentation map of the same drawing: the components drawn exploded on a lettered grid. Ground truth for what "a component" means; compared by shape, not location.
- `IMG_2298.labels.json` — curated labels for the pieces of the map, keyed by piece centroid, used by `biomorphic verify` (check 6).
