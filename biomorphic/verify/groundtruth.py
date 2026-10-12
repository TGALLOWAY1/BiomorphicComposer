"""Ground truth (verify check 6): segment the artist's hand-made segmentation map, label the pieces, and
shape-match every piece against the regions of one session level.

The map (samples/IMG_2298.png) shows the components of the drawing exploded on a pencil grid, each piece
coloured as in the drawing and labelled by hand. Positions differ from the source, so pieces are compared
by *shape*: every mask is normalised into a common frame (crop to bbox, scale so that sqrt(area) is
FRAME_SIZE px, centre on the centroid in a FRAME_CANVAS x FRAME_CANVAS frame) and the score is the IoU
after the best of a few rotations, the horizontal flip and a small translation search. Normalised frames
are tiny, so hundreds of piece x candidate comparisons take a couple of seconds.

Public API
----------
segment_map(map_rgb) -> list[Piece]                      pieces = coloured drawn components of the map
load_labels(path) -> dict ; assign_labels(pieces, labels)  attach curated labels by nearest centroid
shape_score(mask_a, mask_b) -> (iou, ShapeTransform)     scale-normalised IoU of two masks
color_score(lab_a, lab_b) -> 0..1                         mean-Lab similarity (tie-break only)
match_pieces(pieces, candidates) -> MatchTable            best candidate per piece + one-to-one assignment
candidates_from_session(session, level) -> candidates     (id, mask, mean_lab) for every region of a level
render_pieces_overlay(...) / render_match_sheet(...)      images for humans to judge the result
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Hashable, Sequence

import cv2
import numpy as np
from scipy import fft as sfft
from scipy import ndimage as ndi
from scipy.optimize import linear_sum_assignment

from biomorphic.session import rgb_to_lab

# Label grammar: grid row letter + column number (1-7 in rows C/D/E, 4 = symmetry axis; grid cells in rows
# A/B), plus an optional suffix when one handwritten label covers several pieces: -L/-R for a side-by-side
# pair, -T/-M/-B (top/middle/bottom) for pieces stacked on the axis.
LABEL_PATTERN = r"^[A-E][0-7](-[LRTMB])?$"
LABEL_RE = re.compile(LABEL_PATTERN)

FRAME_SIZE = 64        # sqrt(area) of a normalised mask, in frame px
FRAME_CANVAS = 192     # side of the square frame, px (room for shapes up to ~9:1 elongation)

# A piece counts as matched when its scale-normalised shape IoU reaches this. Measured on the sample map
# (every piece matched against the other 29, mirror allowed): mirror twins score median 0.93 (min 0.67, the
# two B4 curls the artist drew differently) while the best *unrelated* piece scores median 0.68, max 0.79,
# and against 80 random ellipses and blobs the best distractor reaches median 0.76, >= 0.8 for a third of
# the pieces. So 0.6 matches almost anything blob-like; 0.8 is where "best match" starts to mean "the same
# component". Judge borderline cases by the margin over the runner-up and prefer the one-to-one assignment.
DEFAULT_THRESHOLD = 0.8
# ±10° in 2.5° steps: the rotation pre-screen is one matrix product, so a finer grid is nearly free, and a
# 5° grid costs long thin shapes up to ~0.15 IoU at the worst residual angle.
DEFAULT_ROTATIONS = (-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0)

Candidate = tuple[Hashable, np.ndarray, Sequence[float]]   # (id, mask, mean_lab); mask may be a bbox crop


# ============================================================================= pieces of the map
@dataclass
class Piece:
    """One coloured component of the segmentation map."""

    index: int
    mask: np.ndarray                          # bool HxW, full map size
    bbox: tuple[int, int, int, int]           # x, y, w, h
    area: int
    centroid: tuple[float, float]             # cx, cy
    mean_lab: tuple[float, float, float]
    label: str | None = None                  # set by assign_labels
    confidence: str | None = None             # "high" | "low", from the labels file
    colour_fraction: float = 0.0              # fraction of pixels with chroma clearly above the paper's

    @property
    def name(self) -> str:
        return self.label if self.label else f"#{self.index}"

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index, "label": self.label, "confidence": self.confidence,
            "bbox": list(self.bbox), "area": self.area,
            "centroid": [round(self.centroid[0], 2), round(self.centroid[1], 2)],
            "mean_lab": [round(v, 2) for v in self.mean_lab],
            "colour_fraction": round(self.colour_fraction, 3),
        }


def estimate_paper(lab: np.ndarray, border_frac: float = 0.02, closing_px: int = 240
                   ) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Paper estimate for a photographed sheet.

    Returns (paper_L_field, (L, a, b)): a smooth per-pixel estimate of the paper's lightness (follows the
    vignetting; grey closing with a window wider than any drawn piece, so the paper is always seen) and the
    median Lab of the image border. The paper is warm-tinted, so chroma is measured relative to its a/b.
    """
    L = lab[..., 0]
    h, w = L.shape
    bw = max(4, int(round(border_frac * min(h, w))))
    border = np.zeros((h, w), bool)
    border[:bw] = border[-bw:] = True
    border[:, :bw] = border[:, -bw:] = True
    paper = tuple(float(np.median(lab[..., c][border])) for c in range(3))
    f = 4
    small = cv2.resize(L, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    k = max(3, (closing_px // f) | 1)
    closed = ndi.grey_closing(small, size=(k, k), mode="nearest")
    closed = cv2.GaussianBlur(closed, (0, 0), max(1.0, k / 12))
    field = cv2.resize(closed, (w, h), interpolation=cv2.INTER_LINEAR)
    return field.astype(np.float32), paper  # type: ignore[return-value]


def segment_map(map_rgb: np.ndarray, *, dark_delta: float = 10.0, chroma_delta: float = 12.0,
                open_px: int = 7, regrow_px: int = 4, min_area: int = 300,
                min_colour_fraction: float = 0.08, border_frac: float = 0.02) -> list[Piece]:
    """Segment the hand-made map into its coloured drawn pieces.

    non-paper = darker than the local paper by `dark_delta` (L units) OR chroma above the paper's by
    `chroma_delta`. An opening of `open_px` removes everything thinner than that (pencil grid lines, the
    handwritten labels, the title box), then the mask is regrown by `regrow_px` inside the non-paper mask so
    the ink outlines and corners the opening ate come back (thin structures stay gone), holes are filled and
    8-connected components taken. Components keep only if they have at least `min_area` px and at least
    `min_colour_fraction` of their pixels are coloured, which rejects any pure-ink leftovers.

    Pieces the artist drew touching (shared ink line, no paper gap) come out as one component; this
    function reports what is there rather than forcing a count. Pieces are numbered in reading order.
    """
    if map_rgb.ndim != 3 or map_rgb.shape[2] != 3:
        raise ValueError("map_rgb must be HxWx3 RGB")
    lab = rgb_to_lab(map_rgb)
    field, (_pL, pa, pb) = estimate_paper(lab, border_frac)
    L = lab[..., 0]
    chroma = np.hypot(lab[..., 1] - pa, lab[..., 2] - pb)
    coloured = chroma > chroma_delta
    nonpaper = ((L < field - dark_delta) | coloured).astype(np.uint8)

    k = max(1, int(open_px))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(nonpaper, cv2.MORPH_OPEN, ker)
    k3 = np.ones((3, 3), np.uint8)
    for _ in range(int(regrow_px)):
        m = cv2.dilate(m, k3) & nonpaper
    m = ndi.binary_fill_holes(m > 0).astype(np.uint8)

    n, lbl, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
    h, w = L.shape
    labf = lab.reshape(-1, 3)
    flat = lbl.ravel()
    sums = [np.bincount(flat, weights=labf[:, c].astype(np.float64), minlength=n) for c in range(3)]
    col_counts = np.bincount(flat, weights=coloured.ravel().astype(np.float64), minlength=n)
    found: list[Piece] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        cf = float(col_counts[i] / area)
        if cf < min_colour_fraction:
            continue
        x, y, bw_, bh = (int(stats[i, j]) for j in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                                   cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        found.append(Piece(
            index=0, mask=lbl == i, bbox=(x, y, bw_, bh), area=area,
            centroid=(float(cents[i][0]), float(cents[i][1])),
            mean_lab=tuple(float(s[i] / area) for s in sums),  # type: ignore[arg-type]
            colour_fraction=cf,
        ))
    row_q = max(1.0, 0.12 * h)   # reading order: coarse rows top to bottom, then left to right
    found.sort(key=lambda p: (round(p.centroid[1] / row_q), p.centroid[0]))
    for i, p in enumerate(found, 1):
        p.index = i
    return found


# ============================================================================= labels
def load_labels(path: Path | str) -> dict[str, Any]:
    """Read a curated labels file: {"map": ..., "pieces": [{"label", "cx", "cy", "area", "confidence", ...}]}."""
    d = json.loads(Path(path).read_text())
    if "pieces" not in d or not isinstance(d["pieces"], list):
        raise ValueError(f"{path}: expected a 'pieces' list")
    for e in d["pieces"]:
        for key in ("label", "cx", "cy", "area"):
            if key not in e:
                raise ValueError(f"{path}: label entry missing {key!r}: {e}")
        if not LABEL_RE.match(str(e["label"])):
            raise ValueError(f"{path}: label {e['label']!r} does not match {LABEL_PATTERN}")
    return d


@dataclass
class LabelAssignment:
    assigned: dict[int, str] = field(default_factory=dict)   # piece index -> label
    distances: dict[int, float] = field(default_factory=dict)
    unmatched: list[dict[str, Any]] = field(default_factory=list)   # label entries with no piece in reach
    unlabelled: list[int] = field(default_factory=list)              # piece indices left without a label

    @property
    def labelled_fraction(self) -> float:
        n = len(self.assigned) + len(self.unlabelled)
        return len(self.assigned) / n if n else 0.0


def assign_labels(pieces: list[Piece], labels: dict[str, Any] | list[dict[str, Any]], max_dist: float = 0.5,
                  min_dist_px: float = 10.0) -> LabelAssignment:
    """Attach labels to pieces by nearest centroid, one-to-one, closest pairs first.

    An entry reaches a piece when their centroids are within max(min_dist_px, max_dist * sqrt(entry area)),
    so a slightly different segmentation of the same map still labels correctly. Pieces are relabelled from
    scratch (label/confidence reset first). Entries that reach no free piece are reported in `unmatched`.
    """
    entries = labels["pieces"] if isinstance(labels, dict) else list(labels)
    for p in pieces:
        p.label = None
        p.confidence = None
    by_index = {p.index: p for p in pieces}
    pairs: list[tuple[float, int, int]] = []
    for ei, e in enumerate(entries):
        tol = max(float(min_dist_px), float(max_dist) * math.sqrt(max(float(e["area"]), 1.0)))
        for p in pieces:
            d = math.hypot(p.centroid[0] - float(e["cx"]), p.centroid[1] - float(e["cy"]))
            if d <= tol:
                pairs.append((d, ei, p.index))
    pairs.sort()
    result = LabelAssignment()
    used_entries: set[int] = set()
    for d, ei, pi in pairs:
        if ei in used_entries or pi in result.assigned:
            continue
        e = entries[ei]
        p = by_index[pi]
        p.label = str(e["label"])
        p.confidence = e.get("confidence")
        result.assigned[pi] = p.label
        result.distances[pi] = d
        used_entries.add(ei)
    result.unmatched = [dict(entries[ei]) for ei in range(len(entries)) if ei not in used_entries]
    result.unlabelled = [p.index for p in pieces if p.index not in result.assigned]
    return result


# ============================================================================= shape matching
@dataclass(frozen=True)
class ShapeTransform:
    """How mask a was moved onto mask b in the normalised frame: optional horizontal flip, then rotation
    about the frame centre, then a shift of (dx, dy) frame px. scale_* are frame px per input px."""

    mirrored: bool
    angle_deg: float
    dx: int
    dy: int
    scale_a: float
    scale_b: float

    def to_json(self) -> dict[str, Any]:
        return {"mirrored": self.mirrored, "angle_deg": self.angle_deg, "dx": self.dx, "dy": self.dy,
                "scale_a": round(self.scale_a, 5), "scale_b": round(self.scale_b, 5)}

    def describe(self) -> str:
        bits = []
        if self.mirrored:
            bits.append("mirror")
        if self.angle_deg:
            bits.append(f"{self.angle_deg:+g}°")
        if self.dx or self.dy:
            bits.append(f"shift ({self.dx:+d},{self.dy:+d})")
        return ", ".join(bits) if bits else "identity"


def normalise_mask(mask: np.ndarray, size: int = FRAME_SIZE, canvas: int = FRAME_CANVAS
                   ) -> tuple[np.ndarray, float]:
    """Crop a mask to its bbox, scale it so sqrt(area) == size px and centre its centroid in a canvas² frame.

    Returns (frame bool canvas x canvas, scale). Parts that do not fit the canvas are clipped.
    """
    mask = np.asarray(mask)
    if mask.dtype != bool:
        mask = mask > 0
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        raise ValueError("empty mask")
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    crop = mask[y0:y1, x0:x1].astype(np.float32)
    scale = float(size) / math.sqrt(float(ys.size))
    new_w = max(1, int(round((x1 - x0) * scale)))
    new_h = max(1, int(round((y1 - y0) * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    small = cv2.resize(crop, (new_w, new_h), interpolation=interp) >= 0.5
    if not small.any():
        small = cv2.resize(crop, (new_w, new_h), interpolation=interp) > 0
    sy, sx = np.nonzero(small)
    cx, cy = float(sx.mean()), float(sy.mean())
    ox = int(round(canvas / 2 - cx))
    oy = int(round(canvas / 2 - cy))
    frame = np.zeros((canvas, canvas), bool)
    # paste with clipping
    src_x0, src_y0 = max(0, -ox), max(0, -oy)
    dst_x0, dst_y0 = max(0, ox), max(0, oy)
    w = min(new_w - src_x0, canvas - dst_x0)
    h = min(new_h - src_y0, canvas - dst_y0)
    if w > 0 and h > 0:
        frame[dst_y0:dst_y0 + h, dst_x0:dst_x0 + w] = small[src_y0:src_y0 + h, src_x0:src_x0 + w]
    return frame, scale


def transform_frame(frame: np.ndarray, mirrored: bool, angle_deg: float, dx: int = 0, dy: int = 0) -> np.ndarray:
    """Apply (flip, rotate about the centre, shift) to a bool frame."""
    out = frame[:, ::-1] if mirrored else frame
    n = frame.shape[0]
    if angle_deg:
        m = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), float(angle_deg), 1.0)
        out = cv2.warpAffine(out.astype(np.float32), m, (n, n), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0.0) >= 0.5
    if dx or dy:
        out = np.roll(out, (int(dy), int(dx)), axis=(0, 1))
    return np.ascontiguousarray(out)


def _variants(frame: np.ndarray, rotations: Sequence[float], allow_mirror: bool
              ) -> tuple[list[tuple[bool, float]], np.ndarray]:
    keys = [(m, float(r)) for m in ((False, True) if allow_mirror else (False,)) for r in rotations]
    stack = np.stack([transform_frame(frame, m, r) for m, r in keys])
    return keys, stack


def _shape_scores(piece_frames: list[np.ndarray], cand_frames: list[np.ndarray], rotations: Sequence[float],
                  allow_mirror: bool, max_shift: int, refine_top: int | None, prune_margin: float | None = None
                  ) -> tuple[np.ndarray, list[list[tuple[bool, float, int, int]]]]:
    """IoU matrix (P x C) and per-pair winning (mirrored, angle, dx, dy).

    Zero-shift IoU for every variant x candidate comes from one matrix product. The translation is then
    refined with an FFT cross-correlation (its argmax within ±max_shift is exactly the shift maximising the
    intersection, hence the IoU) for the `refine_top` best variants of each pair (all when None). With
    `prune_margin`, only candidates whose zero-shift IoU is within that margin of the piece's best are
    refined; the rest keep their zero-shift IoU, a lower bound of the refined value.
    """
    P, C = len(piece_frames), len(cand_frames)
    n = FRAME_CANVAS if not piece_frames else piece_frames[0].shape[0]
    keys, _ = _variants(np.zeros((n, n), bool), rotations, allow_mirror)
    V = len(keys)
    var_stack = np.stack([_variants(f, rotations, allow_mirror)[1] for f in piece_frames])   # (P, V, n, n)
    cand_stack = np.stack(cand_frames)                                                         # (C, n, n)
    Vm = var_stack.reshape(P * V, n * n).astype(np.float32)
    Cm = cand_stack.reshape(C, n * n).astype(np.float32)
    area_v = Vm.sum(axis=1).reshape(P, V)
    area_c = Cm.sum(axis=1)
    inter0 = (Vm @ Cm.T).reshape(P, V, C)
    iou0 = inter0 / np.maximum(area_v[:, :, None] + area_c[None, None, :] - inter0, 1.0)

    k = V if refine_top is None else max(1, min(int(refine_top), V))
    s = int(max_shift)
    idx = np.arange(-s, s + 1) % n
    FC = sfft.rfft2(cand_stack.astype(np.float32), axes=(-2, -1), workers=-1)      # (C, n, n//2+1)
    scores = np.zeros((P, C), np.float32)
    best: list[list[tuple[bool, float, int, int]]] = [[(False, 0.0, 0, 0)] * C for _ in range(P)]
    for p in range(P):
        order_all = np.argsort(-iou0[p], axis=0)[:k]                                 # (k, C) variant ids
        top0 = iou0[p].max(axis=0)                                                   # (C,) best zero-shift IoU
        scores[p] = top0
        for c in range(C):
            v = int(order_all[0, c])
            best[p][c] = (keys[v][0], keys[v][1], 0, 0)
        if prune_margin is None:
            cols = np.arange(C)
        else:
            cols = np.nonzero(top0 >= top0.max() - float(prune_margin))[0]
        if cols.size == 0 or s <= 0:
            continue
        order = order_all[:, cols]                                                   # (k, Cc)
        FV = sfft.rfft2(var_stack[p].astype(np.float32), axes=(-2, -1), workers=-1)  # (V, n, n//2+1)
        prod = np.conj(FV[order]) * FC[None, cols, :, :]                             # (k, Cc, n, n//2+1)
        corr = sfft.irfft2(prod, s=(n, n), axes=(-2, -1), workers=-1)                # (k, Cc, n, n)
        win = corr[:, :, idx[:, None], idx[None, :]]                                 # (k, Cc, 2s+1, 2s+1)
        flat = win.reshape(k, cols.size, -1)
        arg = flat.argmax(axis=2)
        inter = np.take_along_axis(flat, arg[:, :, None], axis=2)[:, :, 0]
        inter = np.clip(np.rint(inter), 0, None)
        a_v = area_v[p][order]                                                       # (k, Cc)
        iou = inter / np.maximum(a_v + area_c[None, cols] - inter, 1.0)
        kb = iou.argmax(axis=0)                                                      # (Cc,)
        refined = iou[kb, np.arange(cols.size)]
        for j, c in enumerate(cols.tolist()):
            if refined[j] < scores[p, c]:
                continue                                                             # keep the zero-shift answer
            scores[p, c] = refined[j]
            v = int(order[kb[j], j])
            a = int(arg[kb[j], j])
            dy, dx = int(idx[a // (2 * s + 1)]), int(idx[a % (2 * s + 1)])
            dy = dy - n if dy > n // 2 else dy
            dx = dx - n if dx > n // 2 else dx
            best[p][c] = (keys[v][0], keys[v][1], dx, dy)
    return scores, best


def shape_score(mask_a: np.ndarray, mask_b: np.ndarray, allow_mirror: bool = True,
                rotations: Sequence[float] = DEFAULT_ROTATIONS, max_shift: int = 8
                ) -> tuple[float, ShapeTransform]:
    """Scale-normalised IoU of two masks after the best flip / rotation / small shift of a onto b."""
    fa, sa = normalise_mask(mask_a)
    fb, sb = normalise_mask(mask_b)
    scores, best = _shape_scores([fa], [fb], rotations, allow_mirror, max_shift, refine_top=None)
    m, ang, dx, dy = best[0][0]
    return float(scores[0, 0]), ShapeTransform(m, ang, dx, dy, sa, sb)


def color_score(lab_a: Sequence[float], lab_b: Sequence[float], scale: float = 25.0) -> float:
    """0..1 similarity of two mean Lab colours: exp(-dE76 / scale). Identical -> 1, dE == scale -> 0.37."""
    d = float(np.linalg.norm(np.asarray(lab_a, np.float64) - np.asarray(lab_b, np.float64)))
    return float(math.exp(-d / float(scale)))


# ============================================================================= matching table
@dataclass
class MatchRow:
    label: str
    piece_index: int
    tile_id: Hashable                 # greedy best candidate by the combined score
    score: float                      # shape IoU of that candidate (what `matched` is judged on)
    colour: float                     # colour similarity of that candidate
    combined: float                   # (1 - w) * shape + w * colour, the ranking score
    matched: bool                     # score >= threshold
    transform: ShapeTransform
    runner_up: float = 0.0            # second-best shape IoU for this piece (how decisive the match is)
    assigned_tile_id: Hashable | None = None   # one-to-one assignment (Hungarian on the combined score)
    assigned_score: float = 0.0
    assigned_matched: bool = False

    @property
    def margin(self) -> float:
        """Best minus runner-up shape IoU. Near zero means the piece would match several regions equally
        (expect that when the candidates include the matched tile's own mirror twin)."""
        return self.score - self.runner_up

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "piece_index": self.piece_index, "tile_id": self.tile_id,
            "score": round(self.score, 4), "colour": round(self.colour, 4), "combined": round(self.combined, 4),
            "matched": self.matched, "transform": self.transform.to_json(), "runner_up": round(self.runner_up, 4),
            "margin": round(self.margin, 4),
            "assigned_tile_id": self.assigned_tile_id, "assigned_score": round(self.assigned_score, 4),
            "assigned_matched": self.assigned_matched,
        }


@dataclass
class MatchTable:
    rows: list[MatchRow]
    threshold: float
    colour_weight: float
    candidate_ids: list[Hashable]
    shape_scores: np.ndarray          # P x C shape IoU matrix (rows in `rows` order)
    skipped_candidates: list[Hashable] = field(default_factory=list)

    @property
    def n_pieces(self) -> int:
        return len(self.rows)

    @property
    def n_candidates(self) -> int:
        return len(self.candidate_ids)

    @property
    def matched_fraction(self) -> float:
        return sum(r.matched for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def assigned_matched_fraction(self) -> float:
        return sum(r.assigned_matched for r in self.rows) / len(self.rows) if self.rows else 0.0

    @property
    def misses(self) -> list[str]:
        """Pieces whose greedy best candidate is below the threshold."""
        return [r.label for r in self.rows if not r.matched]

    @property
    def assigned_misses(self) -> list[str]:
        """Pieces whose one-to-one assigned candidate is below the threshold: the headline misses."""
        return [r.label for r in self.rows if not r.assigned_matched]

    @property
    def reused_tiles(self) -> dict[Hashable, list[str]]:
        """Greedy-best tiles claimed by more than one piece. A blob-like region overlaps most blobby pieces
        at 0.6-0.8 scale-normalised IoU, so it can be 'the best match' of many of them at once; the one-to-one
        assignment is immune to this, which is why it is the headline number."""
        by_tile: dict[Hashable, list[str]] = {}
        for r in self.rows:
            by_tile.setdefault(r.tile_id, []).append(r.label)
        return {t: labs for t, labs in by_tile.items() if len(labs) > 1}

    @property
    def mean_score(self) -> float:
        return float(np.mean([r.score for r in self.rows])) if self.rows else 0.0

    @property
    def baseline(self) -> float:
        """Median shape IoU over every piece x candidate pair: what an unrelated candidate scores.
        Blobby shapes overlap a lot after normalisation, so judge `threshold` against this."""
        return float(np.median(self.shape_scores)) if self.shape_scores.size else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold, "colour_weight": self.colour_weight,
            "n_pieces": self.n_pieces, "n_candidates": self.n_candidates,
            "headline": {"metric": "one-to-one assignment", "matched": sum(r.assigned_matched for r in self.rows),
                         "matched_fraction": round(self.assigned_matched_fraction, 4), "misses": self.assigned_misses},
            "matched": sum(r.matched for r in self.rows), "matched_fraction": round(self.matched_fraction, 4),
            "assigned_matched_fraction": round(self.assigned_matched_fraction, 4),
            "mean_score": round(self.mean_score, 4), "baseline": round(self.baseline, 4), "misses": self.misses,
            "assigned_misses": self.assigned_misses,
            "reused_tiles": {str(t): labs for t, labs in self.reused_tiles.items()},
            "skipped_candidates": list(self.skipped_candidates),
            "rows": [r.to_json() for r in self.rows],
        }

    def to_markdown(self) -> str:
        n = self.n_pieces
        lines = [f"**One-to-one assignment: {sum(r.assigned_matched for r in self.rows)}/{n} pieces matched at "
                 f"shape IoU >= {self.threshold}** ({self.n_candidates} candidates)."]
        if self.assigned_misses:
            lines.append(f"Misses (one-to-one): {', '.join(self.assigned_misses)}.")
        lines.append(f"Greedy best per piece: {sum(r.matched for r in self.rows)}/{n} at the threshold; mean IoU "
                     f"{self.mean_score:.3f}; baseline (median IoU over all piece x candidate pairs) {self.baseline:.3f}.")
        if self.misses:
            lines.append(f"Misses (greedy): {', '.join(self.misses)}.")
        if self.reused_tiles:
            lines.append("Tiles claimed by several pieces (greedy): " +
                         "; ".join(f"{t} <- {', '.join(labs)}" for t, labs in self.reused_tiles.items()) + ".")
        lines.append("")
        lines += ["| map label | 1:1 tile | 1:1 IoU | ok | greedy tile | IoU | runner-up | margin | colour | transform |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in self.rows:
            lines.append(f"| {r.label} | {r.assigned_tile_id if r.assigned_tile_id is not None else '-'} | "
                         f"{r.assigned_score:.3f} | {'yes' if r.assigned_matched else 'no'} | {r.tile_id} | "
                         f"{r.score:.3f} | {r.runner_up:.3f} | {r.margin:+.3f} | {r.colour:.2f} | "
                         f"{r.transform.describe()} |")
        return "\n".join(lines)


def match_pieces(pieces: list[Piece], candidates: Sequence[Candidate], *, threshold: float = DEFAULT_THRESHOLD,
                 colour_weight: float = 0.15, allow_mirror: bool = True,
                 rotations: Sequence[float] = DEFAULT_ROTATIONS, max_shift: int = 8, refine_top: int = 2,
                 prune_margin: float | None = 0.2, min_candidate_area: int = 16,
                 centre_colours: bool = True) -> MatchTable:
    """Best candidate per piece by shape (plus a colour tie-break) and a one-to-one assignment.

    candidates: (id, mask, mean_lab) for the regions of one session level; a mask may be the full image or
    just the region's bbox crop (only the shape matters). The ranking score is
    (1 - colour_weight) * shape_iou + colour_weight * colour; `score` and `matched` (score >= threshold,
    default DEFAULT_THRESHOLD = 0.8, see the note there) use the shape IoU alone so the table stays
    interpretable. The headline number is the one-to-one assignment (`assigned_matched_fraction`): a
    blob-like region can be the greedy best of many pieces at once, the assignment lets it serve one. The
    greedy table stays for diagnosis, with each row's runner-up and margin. With centre_colours the area-weighted mean
    Lab of each side is subtracted first, which cancels the lighting / white-balance difference between the
    map photo and the drawing photo. The translation refinement runs for the `refine_top` best rotations /
    flips of each pair and, with `prune_margin`, only for candidates within that IoU margin of the piece's
    best (the rest keep their zero-shift IoU); None refines every pair.
    """
    if not pieces:
        raise ValueError("no pieces")
    piece_norm = [normalise_mask(p.mask) for p in pieces]
    piece_frames = [f for f, _ in piece_norm]
    piece_scales = [s for _, s in piece_norm]
    ids: list[Hashable] = []
    cand_frames: list[np.ndarray] = []
    cand_scales: list[float] = []
    cand_lab: list[np.ndarray] = []
    cand_area: list[float] = []
    skipped: list[Hashable] = []
    for cid, mask, lab in candidates:
        area = int(np.count_nonzero(mask))
        if area < min_candidate_area:
            skipped.append(cid)
            continue
        ids.append(cid)
        frame, scale = normalise_mask(mask)
        cand_frames.append(frame)
        cand_scales.append(scale)
        cand_lab.append(np.asarray(lab, np.float64))
        cand_area.append(float(area))
    if not ids:
        raise ValueError("no usable candidates")

    shape, best = _shape_scores(piece_frames, cand_frames, rotations, allow_mirror, max_shift, refine_top,
                                prune_margin)
    p_lab = np.array([p.mean_lab for p in pieces], np.float64)
    c_lab = np.stack(cand_lab)
    if centre_colours:
        p_w = np.array([p.area for p in pieces], np.float64)
        c_w = np.array(cand_area)
        p_lab = p_lab - (p_lab * p_w[:, None]).sum(0) / p_w.sum()
        c_lab = c_lab - (c_lab * c_w[:, None]).sum(0) / c_w.sum()
    d = np.linalg.norm(p_lab[:, None, :] - c_lab[None, :, :], axis=2)
    colour = np.exp(-d / 25.0)
    w = float(colour_weight)
    combined = (1.0 - w) * shape + w * colour if w > 0 else shape.copy()

    rows: list[MatchRow] = []
    r_idx, c_idx = linear_sum_assignment(-combined)
    assigned = {int(r): int(c) for r, c in zip(r_idx, c_idx)}
    for i, p in enumerate(pieces):
        j = int(np.argmax(combined[i]))
        m, ang, dx, dy = best[i][j]
        tr = ShapeTransform(m, ang, dx, dy, piece_scales[i], cand_scales[j])
        others = np.delete(shape[i], j)
        row = MatchRow(label=p.name, piece_index=p.index, tile_id=ids[j], score=float(shape[i, j]),
                       colour=float(colour[i, j]), combined=float(combined[i, j]),
                       matched=bool(shape[i, j] >= threshold), transform=tr,
                       runner_up=float(others.max()) if others.size else 0.0)
        if i in assigned:
            a = assigned[i]
            row.assigned_tile_id = ids[a]
            row.assigned_score = float(shape[i, a])
            row.assigned_matched = bool(shape[i, a] >= threshold)
        rows.append(row)
    return MatchTable(rows=rows, threshold=float(threshold), colour_weight=w, candidate_ids=ids,
                      shape_scores=shape, skipped_candidates=skipped)


def candidates_from_session(session: Any, level_index: int, min_area: int = 1) -> list[Candidate]:
    """(region id, bbox-cropped mask, mean_lab) for every region of a session level."""
    label_map = session.label_map(level_index)
    out: list[Candidate] = []
    for rid, info in session.regions(level_index).items():
        if info.area < min_area:
            continue
        x, y, w, h = info.bbox
        out.append((int(rid), label_map[y:y + h, x:x + w] == rid, tuple(info.mean_lab)))
    return out


def match_map_to_session(map_rgb: np.ndarray, session: Any, level_index: int, labels: dict[str, Any] | None = None,
                         **match_kw: Any) -> tuple[list[Piece], MatchTable]:
    """Convenience for check 6: segment the map, label it (when labels are given) and match one level."""
    pieces = segment_map(map_rgb)
    if labels is not None:
        assign_labels(pieces, labels)
    table = match_pieces(pieces, candidates_from_session(session, level_index), **match_kw)
    return pieces, table


def match_map_to_session_levels(map_rgb: np.ndarray, session: Any, labels: dict[str, Any] | None = None,
                                levels: Sequence[int] | None = None, *, threshold: float = DEFAULT_THRESHOLD,
                                max_seconds: float = 60.0, must_include: Sequence[int] = (),
                                **match_kw: Any) -> dict[str, Any]:
    """Evaluate every level of a session against the map (pieces segmented and labelled once).

    Returns a dict with, per level, the one-to-one matched fraction at `threshold` (the headline), the
    greedy "component present" count (pieces whose best region reaches the threshold), mean and baseline
    IoU; per piece, the best (level, region id, score) over the evaluated levels; `best_level` (highest
    one-to-one fraction, ties to the higher mean IoU); `never_matched` (pieces below the threshold at every
    level). Levels are evaluated in order of region count, cheapest first; when the running time passes
    `max_seconds` the remaining levels are subsampled every other one (`subsampled` says so and
    `levels_skipped` lists them), and `must_include` levels are always evaluated. Live objects (pieces, the
    MatchTable per level) ride under underscore keys that `checks.jsonable` strips.
    """
    import time as _time

    t0 = _time.perf_counter()
    pieces = segment_map(map_rgb)
    label_result = assign_labels(pieces, labels) if labels is not None else None
    n_levels = len(session.levels)
    wanted = list(range(n_levels)) if levels is None else [int(l) for l in levels]
    for m in must_include:
        if int(m) not in wanted:
            wanted.append(int(m))
    counts = {int(l["index"]): int(l["n_regions"]) for l in session.levels}
    order = sorted(set(wanted), key=lambda l: (int(l) not in set(int(m) for m in must_include), counts.get(l, 0)))
    tables: dict[int, MatchTable] = {}
    per_level: list[dict[str, Any]] = []
    skipped: list[int] = []
    subsampled = False
    skip_next = False
    for lv in order:
        forced = int(lv) in set(int(m) for m in must_include)
        if not forced and _time.perf_counter() - t0 > max_seconds:
            subsampled = True
            if skip_next:
                skipped.append(int(lv))
                skip_next = False
                continue
            skip_next = True
        t1 = _time.perf_counter()
        cands = candidates_from_session(session, int(lv))
        table = match_pieces(pieces, cands, threshold=threshold, **match_kw)
        tables[int(lv)] = table
        per_level.append({
            "index": int(lv), "n_regions": counts.get(int(lv), len(cands)), "n_candidates": table.n_candidates,
            "one_to_one_matched": sum(r.assigned_matched for r in table.rows),
            "one_to_one_fraction": table.assigned_matched_fraction,
            "greedy_matched": sum(r.matched for r in table.rows), "greedy_fraction": table.matched_fraction,
            "mean_score": table.mean_score, "baseline": table.baseline,
            "reused_tiles": len(table.reused_tiles), "seconds": round(_time.perf_counter() - t1, 3),
        })
    per_level.sort(key=lambda d: d["index"])
    best_per_piece: list[dict[str, Any]] = []
    for i, p in enumerate(pieces):
        best = None
        for lv, table in sorted(tables.items()):  # ascending levels, so ties go to the finest level
            row = table.rows[i]
            if best is None or row.score > best["score"]:
                best = {"label": p.name, "piece_index": p.index, "level": int(lv), "region_id": row.tile_id,
                        "score": float(row.score), "margin": float(row.margin),
                        "transform": row.transform.describe()}
        if best is not None:
            best["matched"] = best["score"] >= threshold
            best_per_piece.append(best)
    best_level = None
    if per_level:
        bl = max(per_level, key=lambda d: (d["one_to_one_fraction"], d["mean_score"]))
        best_level = {"index": bl["index"], "n_regions": bl["n_regions"],
                      "one_to_one_fraction": bl["one_to_one_fraction"], "one_to_one_matched": bl["one_to_one_matched"]}
    return {
        "threshold": float(threshold), "n_pieces": len(pieces),
        "label_assignment": None if label_result is None else {
            "n_assigned": len(label_result.assigned),
            "unmatched_labels": [e.get("label") for e in label_result.unmatched],
            "unlabelled_pieces": list(label_result.unlabelled)},
        "levels": per_level, "best_per_piece": best_per_piece, "best_level": best_level,
        "never_matched": [b["label"] for b in best_per_piece if not b["matched"]],
        "levels_evaluated": sorted(tables), "levels_skipped": sorted(skipped), "subsampled": subsampled,
        "seconds": round(_time.perf_counter() - t0, 3),
        "_pieces": pieces, "_tables": tables,
    }


# ============================================================================= images for humans
def _lab_to_rgb(lab: Sequence[float]) -> tuple[int, int, int]:
    px = np.array([[[lab[0], lab[1], lab[2]]]], np.float32)
    rgb = cv2.cvtColor(px, cv2.COLOR_LAB2RGB)[0, 0]
    return tuple(int(round(float(v) * 255)) for v in np.clip(rgb, 0, 1))


def render_pieces_overlay(map_rgb: np.ndarray, pieces: list[Piece], out_path: Path | str,
                          show_labels: bool = True) -> Path:
    """The map with every piece outlined and numbered (and labelled, when labels are attached)."""
    over = map_rgb.copy()
    for p in pieces:
        x, y, w, h = p.bbox
        pad = 2
        x0, y0 = max(0, x - pad), max(0, y - pad)
        crop = p.mask[y0:y + h + pad, x0:x + w + pad].astype(np.uint8)
        cs, _ = cv2.findContours(crop, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(over, cs, -1, (255, 0, 0), 2, offset=(x0, y0))
    for p in pieces:
        text = str(p.index)
        if show_labels and p.label:
            text = f"{p.index} {p.label}"
        cx, cy = int(p.centroid[0]), int(p.centroid[1])
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        org = (max(0, cx - tw // 2), cy + th // 2)
        cv2.putText(over, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 4, cv2.LINE_AA)
        cv2.putText(over, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (190, 0, 0), 2, cv2.LINE_AA)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(over, cv2.COLOR_RGB2BGR))
    return out


def render_match_sheet(pieces: list[Piece], candidates: Sequence[Candidate], table: MatchTable,
                       out_path: Path | str, cell_px: int = 128, columns: int = 3) -> Path:
    """One cell per piece: its normalised silhouette (filled with its mean colour), the best candidate's,
    and an overlay of the transformed piece (blue) on the candidate (orange; overlap dark) with the score."""
    cand = {cid: (mask, lab) for cid, mask, lab in candidates}
    by_index = {p.index: p for p in pieces}
    n = FRAME_CANVAS
    pad, head = 6, 22
    cw = 3 * cell_px + 4 * pad
    ch = cell_px + head + 2 * pad
    rows = sorted(table.rows, key=lambda r: (r.label is None, str(r.label)))
    n_rows = max(1, math.ceil(len(rows) / columns))
    sheet = np.full((n_rows * ch, columns * cw, 3), 245, np.uint8)
    paper = (236, 231, 220)

    def panel(mask_frame: np.ndarray, colour: tuple[int, int, int]) -> np.ndarray:
        img = np.full((n, n, 3), paper, np.uint8)
        img[mask_frame] = colour
        return cv2.resize(img, (cell_px, cell_px), interpolation=cv2.INTER_AREA)

    for k, r in enumerate(rows):
        p = by_index[r.piece_index]
        pf, _ = normalise_mask(p.mask)
        pc = _lab_to_rgb(p.mean_lab)
        mask_b, lab_b = cand[r.tile_id]
        cf, _ = normalise_mask(mask_b)
        cc = _lab_to_rgb(lab_b)
        tp = transform_frame(pf, r.transform.mirrored, r.transform.angle_deg, r.transform.dx, r.transform.dy)
        ov = np.full((n, n, 3), paper, np.uint8)
        ov[cf] = (235, 150, 60)
        ov[tp] = (70, 120, 220)
        ov[cf & tp] = (40, 40, 70)
        ov = cv2.resize(ov, (cell_px, cell_px), interpolation=cv2.INTER_AREA)
        gx, gy = (k % columns) * cw, (k // columns) * ch
        y0 = gy + head + pad
        for j, img in enumerate((panel(pf, pc), panel(cf, cc), ov)):
            x0 = gx + pad + j * (cell_px + pad)
            sheet[y0:y0 + cell_px, x0:x0 + cell_px] = img
        ok = (40, 150, 60) if r.matched else (200, 40, 40)
        cv2.rectangle(sheet, (gx + 2, gy + 2), (gx + cw - 3, gy + ch - 3), ok, 2)
        text = f"{r.label} -> {r.tile_id}  IoU {r.score:.2f}  col {r.colour:.2f}  {r.transform.describe()}"
        cv2.putText(sheet, text, (gx + pad, gy + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (30, 30, 30), 1, cv2.LINE_AA)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    return out
