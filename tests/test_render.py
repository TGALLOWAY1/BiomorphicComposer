import numpy as np
import pytest

from biomorphic.library import render
from biomorphic.library.render import MODES, inkness, mean_lab
from biomorphic.session import Session, rgb_to_lab
from biomorphic.testing import INK, RECTS

INK_RGB = (20, 20, 20)
BLUE = RECTS[1][4]


def _lab(rgb) -> np.ndarray:
    return rgb_to_lab(np.array([[rgb]], np.uint8))[0, 0].astype(np.float64)


@pytest.fixture
def tile(synthetic_dir) -> np.ndarray:
    """Cell 1 of the synthetic session: a blue rectangle with a 4-px black ink border, fully opaque."""
    return Session.load(synthetic_dir).cut_tile(0, 1)[0]


def _palette_for(rgba):
    m = mean_lab(rgba)
    return [{"from": list(m), "to": [m[0] + 10, m[1] + 25, m[2] + 30]}]


def test_modes_tuple():
    assert MODES == ("original", "greyscale", "outline", "remap")


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_leaves_input_untouched_and_keeps_shape(tile, mode):
    before = tile.tobytes()
    out = render.render(tile, mode, palette=_palette_for(tile))
    assert tile.tobytes() == before
    assert out.shape == tile.shape and out.dtype == np.uint8
    assert out is not tile
    if mode != "outline":
        assert np.array_equal(out[..., 3], tile[..., 3])
    # writing into the result must not leak into the input either
    out[...] = 7
    assert tile.tobytes() == before


def test_original_is_an_equal_copy(tile):
    out = render.render(tile, "original")
    assert np.array_equal(out, tile)
    assert not np.shares_memory(out, tile)


def test_greyscale_is_neutral_and_keeps_lightness_order(tile):
    out = render.render(tile, "greyscale")
    assert np.array_equal(out[..., 0], out[..., 1]) and np.array_equal(out[..., 1], out[..., 2])
    ink_grey = int(out[0, 0, 0])            # border pixel
    fill_grey = int(out[INK + 5, INK + 5, 0])  # inside the blue fill
    assert ink_grey < 40 < fill_grey        # the fill (L ~ 50) is lighter than the ink (L ~ 6)
    assert abs(fill_grey - render.grey_from_L(np.array(_lab(BLUE)[0]))) <= 1
    # an all-grey (a = b = 0) input maps to itself within rounding
    grey_in = np.dstack([np.full((3, 3, 3), 128, np.uint8), np.full((3, 3), 255, np.uint8)])
    assert np.abs(render.render(grey_in, "greyscale")[..., :3].astype(int) - 128).max() <= 1


def test_outline_keeps_only_the_ink(tile):
    out = render.render(tile, "outline")
    assert not out[..., :3].any()                          # lines are black
    assert out[..., 3][INK:-INK, INK:-INK].max() == 0      # nothing survives inside the flat colour fill
    ring = np.ones(tile.shape[:2], bool)
    ring[INK:-INK, INK:-INK] = False
    assert out[..., 3][ring].min() == 255                  # the drawn border is fully kept


def test_outline_alpha_is_tile_alpha_times_inkness():
    rgba = np.zeros((4, 6, 4), np.uint8)
    rgba[..., :3] = INK_RGB                                # ink colour everywhere
    rgba[:, :2, 3] = 255
    rgba[:, 2:4, 3] = 128
    rgba[:, 4:, 3] = 0                                     # outside the region: must stay invisible
    out = render.render(rgba, "outline")
    assert out[:, :2, 3].min() == 255
    assert out[:, 2:4, 3].min() == 128 and out[:, 2:4, 3].max() == 128
    assert out[:, 4:, 3].max() == 0


def test_outline_threshold_is_a_smooth_ramp_not_a_hard_cut():
    # neutral greys from black to white: inkness must fall from 1 to 0 through intermediate values
    levels = np.arange(0, 256, 8, dtype=np.uint8)
    rgba = np.zeros((1, len(levels), 4), np.uint8)
    rgba[0, :, 0] = rgba[0, :, 1] = rgba[0, :, 2] = levels
    rgba[..., 3] = 255
    ink = inkness(rgba)[0]
    assert ink[0] == 1.0 and ink[-1] == 0.0
    assert np.all(np.diff(ink) <= 1e-6)                    # monotone
    assert ((ink > 0.05) & (ink < 0.95)).sum() >= 2        # a real ramp, not a step
    alpha = render.render(rgba, "outline")[0, :, 3]
    assert ((alpha > 0) & (alpha < 255)).any()
    # dark but coloured (navy pencil-like) is not ink; dark and colourless is
    navy = np.array([[[40, 45, 90, 255]]], np.uint8)
    black = np.array([[[10, 10, 10, 255]]], np.uint8)
    assert inkness(navy)[0, 0] < 0.05 < 0.95 < inkness(black)[0, 0]


def test_mean_lab_uses_only_visible_pixels():
    rgba = np.zeros((4, 4, 4), np.uint8)
    rgba[:, :2, :3] = BLUE
    rgba[:, :2, 3] = 255
    rgba[:, 2:, :3] = INK_RGB                              # hidden pixels must not count
    rgba[:, 2:, 3] = 0
    L, a, b = mean_lab(rgba)
    assert np.allclose((L, a, b), _lab(BLUE), atol=0.05)
    assert 0.0 <= L <= 100.0
    assert mean_lab(np.zeros((2, 2, 4), np.uint8)) == (0.0, 0.0, 0.0)


def test_mean_lab_matches_the_session_region_stats(synthetic_dir):
    s = Session.load(synthetic_dir)
    rgba, _ = s.cut_tile(0, 2)
    assert np.allclose(mean_lab(rgba), s.regions(0)[2].mean_lab, atol=0.01)


def test_remap_moves_mean_lab_toward_the_target(tile):
    m = np.asarray(mean_lab(tile))
    target = m + np.array([10.0, 25.0, 30.0])
    decoy_from = m + np.array([0.0, 60.0, 60.0])           # far from the tile's colour: must not be picked
    palette = [
        {"from": decoy_from.tolist(), "to": (decoy_from - np.array([10.0, 25.0, 30.0])).tolist()},
        {"from": m.tolist(), "to": target.tolist()},
    ]
    assert render.nearest_palette_entry(m, palette) == 1
    out = render.render(tile, "remap", palette=palette)
    m2 = np.asarray(mean_lab(out))
    assert np.linalg.norm(m2 - target) < np.linalg.norm(m - target)
    assert m2[1] > m[1] and m2[2] > m[2]                   # moved in the direction of the chosen entry
    assert np.array_equal(out[..., 3], tile[..., 3])


def test_remap_with_identity_entry_is_a_near_noop(tile):
    m = list(mean_lab(tile))
    out = render.render(tile, "remap", palette=[{"from": m, "to": m}])
    assert np.abs(out[..., :3].astype(int) - tile[..., :3].astype(int)).max() <= 2


def test_bad_inputs_raise(tile):
    with pytest.raises(ValueError):
        render.render(tile, "sepia")
    with pytest.raises(ValueError):
        render.render(tile, "remap")
    with pytest.raises(ValueError):
        render.render(tile, "remap", palette=[])
    with pytest.raises(ValueError):
        render.render(tile, "remap", palette=[{"from": [0, 0, 0]}])
    with pytest.raises(ValueError):
        render.render(tile.astype(np.float32), "original")
    with pytest.raises(ValueError):
        render.render(tile[..., 0], "original")


def test_rgb_input_gets_an_opaque_alpha(tile):
    out = render.render(np.ascontiguousarray(tile[..., :3]), "greyscale")
    assert out.shape == tile.shape and out[..., 3].min() == 255
