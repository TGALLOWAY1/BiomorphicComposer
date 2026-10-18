"""fold step: where every point of the drawing lands when the page is folded along its axis.

Folding along the axis lays each component on top of its twin, but hand-drawn halves are never exact mirror
images: the centre line leans and wanders, and one half comes out a little wider or taller. So the fold is a
reflection about the axis followed by an alignment of the mirrored image onto the original:
  1. an affine map (tilt, stretch, shift), found by ECC on blurred ink images over the left half, and
  2. a smooth residual displacement field for the drift an affine cannot follow: DIS optical flow on the
     lightness channel, smoothed with a Gaussian of ~1.4 % of the figure width and clipped, so it can bend
     the fold gently but cannot move one component onto another.
For every pixel p the result gives twin(p), the point whose mirror image lands on p. The alignment is fitted on the
left half, so there twin(p) = R(G(p)) with G(p) = A [p + f(p); 1] and R(x, y) = (2 * axis_x - 1 - x, y) (pixel
convention of pairing.py); on the right half twin is the inverse of that map, the q with R(G(q)) = p, found by
fixed-point iteration, so that twin(twin(p)) = p everywhere (a vertical offset between the halves maps down on one
side and back up on the other). The halves meet on the local centre line, not the straight axis.
The local centre line is where twin(p) = p; the centre band is that curve widened by a tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

DEFAULTS = {
    "fold_flow_sigma_frac": 0.014,   # smoothing of the residual field, as a fraction of the figure width
    "fold_flow_clip_frac": 0.015,    # residual displacement clipped to this fraction of the figure width
    "fold_band_frac": 0.012,         # centre band half-width: this fraction of the figure width ...
    "fold_band_min_t0": 3.0,         # ... but at least this many ink half-thicknesses + 4 px
    "fold_store_step": 8,            # the smooth field is stored at 1/step resolution
}

FOLD_FILE = "fold.npz"


def reflect_columns(w: int, axis_x: float) -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(w)
    xr = np.floor(2.0 * float(axis_x) - xs - 0.5).astype(np.int64)
    return xr, (xr >= 0) & (xr < w)


def reflect(img: np.ndarray, axis_x: float) -> np.ndarray:
    xr, ok = reflect_columns(img.shape[1], axis_x)
    out = np.zeros_like(img)
    out[:, ok] = img[:, xr[ok]]
    return out


@dataclass
class Fold:
    axis_x: float
    affine: np.ndarray            # 2x3, original coords (after the residual field) -> mirror-image coords
    field: np.ndarray             # (hs, ws, 2) float32 residual displacement, 1/step resolution
    shape: tuple[int, int]        # (H, W) of the working copy
    band_half_width: float
    meta: dict = field(default_factory=dict)
    _twin: tuple[np.ndarray, np.ndarray] | None = None

    # ---- geometry --------------------------------------------------------------------------------
    def twin_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """float32 HxW maps (tx, ty): the twin location of every pixel (an involution)."""
        if self._twin is None:
            h, w = self.shape
            f = cv2.resize(self.field, (w, h), interpolation=cv2.INTER_LINEAR)
            f0, f1 = np.ascontiguousarray(f[..., 0]), np.ascontiguousarray(f[..., 1])
            a = self.affine.astype(np.float32)

            def g(qx: np.ndarray, qy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
                """G(q) = A [q + f(q); 1], with the residual field sampled at q (2-D maps: remap's limit)."""
                fx = cv2.remap(f0, qx, qy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                fy = cv2.remap(f1, qx, qy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                px, py = qx + fx, qy + fy
                return a[0, 0] * px + a[0, 1] * py + a[0, 2], a[1, 0] * px + a[1, 1] * py + a[1, 2]

            c = np.float32(2.0 * self.axis_x - 1.0)
            gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            ux, uy = g(gx, gy)
            tx, ty = c - ux, uy                                   # forward map R(G(p))
            # The halves meet on the local centre line, where the forward map's twin stops lying to the right;
            # splitting on the straight line instead would leave the strip between the two lines inconsistent.
            right = tx <= gx
            cols = np.nonzero(right.any(axis=0))[0]
            if cols.size:
                x0 = int(cols[0])
                sub = right[:, x0:]
                rx = np.ascontiguousarray(c - gx[:, x0:])         # right half: solve G(q) = R(p)
                ry = np.ascontiguousarray(gy[:, x0:])
                qx, qy = rx.copy(), ry.copy()
                for _ in range(20):    # G is near the identity; where the field is steep convergence is slower
                    gqx, gqy = g(qx, qy)
                    dx, dy = gqx - rx, gqy - ry
                    qx -= dx
                    qy -= dy
                    if max(np.abs(dx[sub]).max(), np.abs(dy[sub]).max()) < 0.05:
                        break
                tx[:, x0:] = np.where(sub, qx, tx[:, x0:])
                ty[:, x0:] = np.where(sub, qy, ty[:, x0:])
            self._twin = (tx, ty)
        return self._twin

    def mirror(self, img: np.ndarray) -> np.ndarray:
        """img sampled at every pixel's twin (nearest), 0 where the twin falls off the image: lay the other
        half on top. For a label map this answers 'which region is my twin's'."""
        tx, ty = self.twin_maps()
        h, w = self.shape
        xi, yi = np.rint(tx).astype(np.int64), np.rint(ty).astype(np.int64)
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        out = np.zeros_like(img)
        out[ok] = img[yi[ok], xi[ok]]
        return out

    def axis_curve(self, figure_mask: np.ndarray, step: int = 8) -> np.ndarray:
        """(n, 2) array of (y, x): the local centre line, where a point is its own twin, rows inside the figure."""
        tx, _ty = self.twin_maps()
        h, w = self.shape
        rows = np.nonzero(figure_mask.any(axis=1))[0]
        pts = []
        x0 = int(round(self.axis_x))
        lo, hi = max(1, x0 - 120), min(w - 1, x0 + 120)
        for y in range(int(rows.min()), int(rows.max()) + 1, step) if rows.size else []:
            g = tx[y, lo:hi] - np.arange(lo, hi, dtype=np.float32)      # > 0 left of the line, < 0 right of it
            s = np.nonzero((g[:-1] > 0) & (g[1:] <= 0))[0]
            if s.size == 0:
                continue
            i = s[np.argmin(np.abs(s + lo - self.axis_x))]
            x = lo + i + g[i] / max(1e-6, g[i] - g[i + 1])
            pts.append((float(y), float(x)))
        if not pts:
            return np.array([[0.0, self.axis_x], [float(h - 1), self.axis_x]])
        arr = np.array(pts)
        if len(arr) >= 5:   # a running median removes isolated crossings caused by the residual field
            k = 5
            pad = np.pad(arr[:, 1], k // 2, mode="edge")
            arr[:, 1] = np.array([np.median(pad[i:i + k]) for i in range(len(arr))])
        return arr

    def axis_x_at(self, curve: np.ndarray, y: np.ndarray | float) -> np.ndarray:
        return np.interp(np.asarray(y, dtype=np.float64), curve[:, 0], curve[:, 1])

    # ---- storage ---------------------------------------------------------------------------------
    def save(self, session_dir: Path | str) -> Path:
        path = Path(session_dir) / FOLD_FILE
        np.savez_compressed(path, affine=self.affine.astype(np.float64), field=self.field.astype(np.float32),
                            shape=np.array(self.shape), axis_x=np.array(self.axis_x),
                            band=np.array(self.band_half_width))
        return path

    @classmethod
    def load(cls, session_dir: Path | str) -> "Fold | None":
        path = Path(session_dir) / FOLD_FILE
        if not path.exists():
            return None
        d = np.load(path)
        return cls(axis_x=float(d["axis_x"]), affine=d["affine"].astype(np.float32), field=d["field"],
                   shape=tuple(int(v) for v in d["shape"]), band_half_width=float(d["band"]))


def _ecc_affine(template: np.ndarray, moving: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, float]:
    """Affine warp W with template(q) ~ moving(W [q; 1]) on mask; identity when ECC does not converge."""
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    try:
        sc = 0.5
        t_s = cv2.resize(template, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        m_s = cv2.resize(moving, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        k_s = cv2.resize(mask.astype(np.uint8), (t_s.shape[1], t_s.shape[0]), interpolation=cv2.INTER_NEAREST)
        _cc, warp = cv2.findTransformECC(t_s, m_s, warp, cv2.MOTION_AFFINE, crit, k_s, 5)
        warp[:, 2] /= sc
        cc, warp = cv2.findTransformECC(template, moving, warp, cv2.MOTION_AFFINE, crit, mask.astype(np.uint8), 5)
        return warp, float(cc)
    except cv2.error:
        return np.eye(2, 3, dtype=np.float32), 0.0


def estimate_fold(lab: np.ndarray, ink_mask: np.ndarray, figure_mask: np.ndarray, axis_x: float, t0: float,
                  params: dict | None = None) -> Fold:
    p = {**DEFAULTS, **(params or {})}
    h, w = figure_mask.shape
    fig = figure_mask.astype(bool)
    xs = np.nonzero(fig.any(axis=0))[0]
    fig_w = float(xs.max() - xs.min() + 1) if xs.size else float(w)
    sig_ink = max(1.5, 1.5 * float(t0))
    template = cv2.GaussianBlur(ink_mask.astype(np.float32), (0, 0), sig_ink)
    moving = cv2.GaussianBlur(reflect(ink_mask.astype(np.float32), axis_x), (0, 0), sig_ink)
    left = fig & (np.arange(w)[None, :] < axis_x - 2 * t0 - 4)
    affine, cc = _ecc_affine(template, moving, left)

    # residual field on lightness: original vs the affine-aligned mirror, inside both silhouettes
    L = lab[..., 0].astype(np.float32)
    inv = cv2.WARP_INVERSE_MAP
    mL = cv2.warpAffine(reflect(L, axis_x), affine, (w, h), flags=cv2.INTER_LINEAR | inv)
    mfig = cv2.warpAffine(reflect(fig.astype(np.uint8), axis_x), affine, (w, h), flags=cv2.INTER_NEAREST | inv) > 0
    both = fig & mfig
    a8 = np.clip(np.where(both, L * 2.55, 0), 0, 255).astype(np.uint8)
    b8 = np.clip(np.where(both, mL * 2.55, 0), 0, 255).astype(np.uint8)
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(a8, b8, None)
    sig = max(2.0, float(p["fold_flow_sigma_frac"]) * fig_w)
    # the residual belongs to the forward map, which serves the left half; the right half uses its inverse
    fit = both & (np.arange(w)[None, :] < axis_x - 0.5)
    wgt = cv2.GaussianBlur(fit.astype(np.float32), (0, 0), sig)
    f = np.stack([cv2.GaussianBlur(flow[..., c] * fit, (0, 0), sig) / np.maximum(wgt, 1e-3) for c in range(2)], -1)
    clip = float(p["fold_flow_clip_frac"]) * fig_w
    f = np.clip(f, -clip, clip).astype(np.float32)
    step = max(1, int(p["fold_store_step"]))
    small = cv2.resize(f, ((w + step - 1) // step, (h + step - 1) // step), interpolation=cv2.INTER_AREA)
    band = max(float(p["fold_band_frac"]) * fig_w, float(p["fold_band_min_t0"]) * float(t0) + 4.0)
    fold = Fold(axis_x=float(axis_x), affine=affine.astype(np.float32), field=small, shape=(h, w), band_half_width=band)

    # how much better the halves line up after the fold (ink of the left half on the folded right half)
    mink_plain = reflect(ink_mask, axis_x)
    mink_fold = fold.mirror(ink_mask)
    near = cv2.dilate(ink_mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    lh = left & ink_mask
    denom = max(1, int(lh.sum()))
    rightside = fig & (np.arange(w)[None, :] > axis_x + 2 * t0 + 4)
    rdenom = max(1, int((rightside & ink_mask).sum()))
    mag = np.hypot(f[..., 0], f[..., 1])[fit]
    fold.meta = {
        "affine": [[round(float(v), 6) for v in row] for row in affine],
        "ecc": round(cc, 4),
        "rotation_deg": round(float(np.degrees(np.arctan2(affine[1, 0], affine[0, 0]))), 3),
        "shear": round(float(affine[0, 1]), 5),
        "scale_xy": [round(float(np.hypot(affine[0, 0], affine[1, 0])), 5),
                     round(float(np.hypot(affine[0, 1], affine[1, 1])), 5)],
        "field_px": {"p50": round(float(np.percentile(mag, 50)), 2) if mag.size else 0.0,
                     "p90": round(float(np.percentile(mag, 90)), 2) if mag.size else 0.0,
                     "sigma": round(sig, 1), "clip": round(clip, 1)},
        "ink_on_folded_ink": {"plain": round(float((mink_plain & near & left).sum()) / denom, 4),
                              "fold": round(float((mink_fold & near & left).sum()) / denom, 4),
                              "fold_right": round(float((mink_fold & near & rightside).sum()) / rdenom, 4)},
        "band_half_width": round(band, 1),
    }
    curve = fold.axis_curve(fig, step=16)
    fold.meta["axis_curve"] = [[round(float(y), 1), round(float(x), 2)] for y, x in curve]
    return fold
