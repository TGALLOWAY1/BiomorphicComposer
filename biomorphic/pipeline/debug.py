"""Debug images for humans: ink over the photo, the axis, fragments, boundaries at three levels, pairs,
and a contact sheet that tiles them (`biomorphic sheet`). Everything is read back from a session directory.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from biomorphic.session import Session

_PALETTE_SEED = 7


def boundary_mask(label_map: np.ndarray, internal_only: bool = True) -> np.ndarray:
    """Pixels whose 4-neighbour carries a different label (both labels nonzero when internal_only)."""
    lm = label_map
    out = np.zeros(lm.shape, bool)
    for dy, dx in ((0, 1), (1, 0)):
        a = lm[: lm.shape[0] - dy, : lm.shape[1] - dx]
        b = lm[dy:, dx:]
        diff = a != b
        if internal_only:
            diff &= (a > 0) & (b > 0)
        out[: lm.shape[0] - dy, : lm.shape[1] - dx] |= diff
        out[dy:, dx:] |= diff
    return out


def random_colours(n: int, seed: int = _PALETTE_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    cols = rng.integers(40, 230, size=(n + 1, 3)).astype(np.uint8)
    cols[0] = 0
    return cols


def blend(rgb: np.ndarray, mask: np.ndarray, colour: tuple[int, int, int], alpha: float) -> np.ndarray:
    out = rgb.astype(np.float32)
    col = np.array(colour, np.float32)
    out[mask] = out[mask] * (1.0 - alpha) + col * alpha
    return out.astype(np.uint8)


def draw_axis(rgb: np.ndarray, axis_x: float, colour=(255, 40, 40)) -> np.ndarray:
    out = rgb.copy()
    x = int(round(axis_x))
    cv2.line(out, (x, 0), (x, out.shape[0] - 1), colour, 2)
    return out


def draw_boundaries(rgb: np.ndarray, label_map: np.ndarray, colour=(255, 255, 0), thick: int = 2) -> np.ndarray:
    b = boundary_mask(label_map, internal_only=False)
    if thick > 1:
        b = cv2.dilate(b.astype(np.uint8), np.ones((thick, thick), np.uint8)) > 0
    out = rgb.copy()
    out[b] = colour
    return out


def level_nearest(session: Session, n_regions: int) -> int:
    return int(min(session.levels, key=lambda lv: abs(lv["n_regions"] - n_regions))["index"])


def pairs_image(session: Session, level: int) -> np.ndarray:
    """Twins share a colour; axis regions grey; unpaired off-axis regions red; axis line drawn."""
    lm = session.label_map(level)
    pairing = session.pairing(level)
    sides, twin = pairing["sides"], pairing["twin"]
    ids = [rid for rid in sides]
    colours = random_colours(len(pairing["pairs"]) + 1, seed=3)
    out = session.working_rgb.copy().astype(np.float32)
    colour_of: dict[int, np.ndarray] = {}
    for i, pr in enumerate(pairing["pairs"]):
        colour_of[pr["a"]] = colours[i + 1]
        colour_of[pr["b"]] = colours[i + 1]
    for rid in ids:
        m = lm == rid
        if sides[rid] == "axis":
            col, alpha = np.array((150, 150, 150), np.float32), 0.45
        elif rid in colour_of:
            col, alpha = colour_of[rid].astype(np.float32), 0.55
        else:
            col, alpha = np.array((255, 0, 0), np.float32), 0.6
        out[m] = out[m] * (1.0 - alpha) + col * alpha
    out = out.astype(np.uint8)
    b = boundary_mask(lm, internal_only=False)
    out[b] = (0, 0, 0)
    out = draw_axis(out, session.axis_x, (255, 255, 255))
    for pr in pairing["pairs"]:
        ca = session.regions(level)[pr["a"]].centroid
        cb = session.regions(level)[pr["b"]].centroid
        cv2.line(out, (int(ca[0]), int(ca[1])), (int(cb[0]), int(cb[1])), (255, 255, 255), 1)
    return out


def write_debug_images(session_dir: Path | str) -> dict[str, Path]:
    """Write the debug PNGs into <session>/debug/ and return {name: path}."""
    s = Session.load(session_dir)
    dbg = s.path / "debug"
    dbg.mkdir(exist_ok=True)
    rgb = s.working_rgb
    out: dict[str, Path] = {}

    def save(name: str, img: np.ndarray) -> None:
        path = dbg / f"{name}.png"
        cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        out[name] = path

    ink_img = blend(rgb, s.ink_mask, (255, 0, 0), 0.75)
    fig_edge = boundary_mask(s.figure_mask.astype(np.int32), internal_only=False)
    ink_img[fig_edge] = (0, 255, 0)
    save("ink", ink_img)
    save("axis", draw_axis(blend(rgb, s.figure_mask, (0, 0, 255), 0.15), s.axis_x))
    cols = random_colours(int(s.fragments.max()))
    frag_img = cols[s.fragments]
    frag_img[~s.figure_mask] = rgb[~s.figure_mask]
    frag_img = (0.55 * frag_img.astype(np.float32) + 0.45 * rgb.astype(np.float32)).astype(np.uint8)
    frag_img[boundary_mask(s.fragments)] = (0, 0, 0)
    save("fragments", frag_img)
    picks = sorted({0, level_nearest(s, 60), level_nearest(s, 15)})
    for lv in picks:
        save(f"boundaries_L{lv:02d}", draw_boundaries(rgb, s.label_map(lv)))
    lv60 = level_nearest(s, 60)
    save(f"pairs_L{lv60:02d}", pairs_image(s, lv60))
    return out


def _caption(img: np.ndarray, text: str) -> np.ndarray:
    bar = np.full((36, img.shape[1], 3), 255, np.uint8)
    cv2.putText(bar, text, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def write_debug_sheet(session_dir: Path | str, out_path: Path | str | None = None, tile_width: int = 600) -> Path:
    """One contact-sheet PNG from an existing session: ink, axis, fragments, three boundary levels, pairs."""
    s = Session.load(session_dir)
    images = write_debug_images(session_dir)
    n_levels = len(s.levels)
    order = ["ink", "axis", "fragments"] + sorted(k for k in images if k.startswith("boundaries")) + \
            sorted(k for k in images if k.startswith("pairs"))
    tiles = []
    for name in order:
        img = cv2.cvtColor(cv2.imread(str(images[name]), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        scale = tile_width / w
        img = cv2.resize(img, (tile_width, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        label = name
        if name.startswith(("boundaries", "pairs")):
            lv = int(name.split("_L")[1])
            info = s.levels[lv]
            label = f"{name.split('_')[0]} level {lv}/{n_levels - 1}: {info['n_regions']} regions"
            if name.startswith("pairs"):
                pr = s.pairing(lv)
                sides = pr["sides"]
                label += (f", {sum(v == 'axis' for v in sides.values())} axis, "
                          f"{sum(v == 'L' for v in sides.values())} L, {sum(v == 'R' for v in sides.values())} R, "
                          f"{len(pr['pairs'])} pairs")
        elif name == "axis":
            label = f"axis x={s.axis_x:.1f} score={s.meta['axis']['score']:.3f} iou={s.meta['axis']['mirror_iou']:.3f}"
        elif name == "fragments":
            label = f"fragments: {s.meta['counts']['fragments']} leaves"
        elif name == "ink":
            label = f"ink mask (t0={s.meta.get('measured', {}).get('ink_t0', 0):.1f} px)"
        tiles.append(_caption(img, label))
    th = max(t.shape[0] for t in tiles)
    tiles = [np.vstack([t, np.full((th - t.shape[0], t.shape[1], 3), 255, np.uint8)]) for t in tiles]
    cols = 4 if len(tiles) > 4 else len(tiles)
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        while len(row) < cols:
            row.append(np.full_like(tiles[0], 255))
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    out = Path(out_path) if out_path else s.path / "debug" / "sheet.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    return out
