"""Default display names for tiles: band letter + column number, the scheme of the reel's part ids.

A name says where on the drawing a part sits, so a pair reads as a pair and the grid sorts top to bottom:

  letter  A..E   which fifth of the working copy's height the part's centre falls in, top to bottom
  number  1..7   where it sits across the drawing: 4 is on the axis, 3 / 2 / 1 step out to the viewer's left,
                 5 / 6 / 7 to the right, so mirror twins are 3 <-> 5, 2 <-> 6, 1 <-> 7
  suffix  b, c…  when the name is taken within the same source drawing (a twin pair shares its suffix)

Everything here is a pure function of what provenance already stores (the bounding box, the axis and the working
copy's size), so a library written before names existed can be named after the fact. A name is a label, not an
identity: the tile id stays the key, and the artist may rename a tile (`Library.rename`).
"""
from __future__ import annotations

import math
import re

BANDS = "ABCDE"
COLUMNS = 3                                    # steps out from the axis on each side
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,23}$")
_AUTO_RE = re.compile(r"^([A-E])([1-7])([a-z]*)$")

__all__ = ["BANDS", "NAME_RE", "base_name", "pair_base_names", "mirror_name", "suffixes", "valid_name"]


def _band(cy: float, height: float) -> str:
    i = int(float(cy) / max(float(height), 1.0) * len(BANDS))
    return BANDS[min(len(BANDS) - 1, max(0, i))]


def _step(dx: float, axis_x: float, width: float) -> int:
    half = max(float(axis_x), float(width) - float(axis_x), 1.0)
    return min(COLUMNS, max(1, math.ceil(abs(float(dx)) / half * COLUMNS)))


def base_name(cx: float, cy: float, axis_x: float, width: float, height: float, side: str | None) -> str:
    """The name of a part centred on (cx, cy), before any suffix. `side` is 'L' | 'R' | 'axis' (None: by position)."""
    if side not in ("L", "R", "axis"):
        side = "L" if cx < axis_x else "R"
    if side == "axis":
        return f"{_band(cy, height)}4"
    k = _step(cx - axis_x, axis_x, width)
    return f"{_band(cy, height)}{4 - k if side == 'L' else 4 + k}"


def pair_base_names(ca: tuple[float, float], cb: tuple[float, float], side_a: str | None, side_b: str | None,
                    axis_x: float, width: float, height: float) -> tuple[str, str] | None:
    """Names for a mirror pair, from the pair as a whole: one band, mirrored numbers. None when the two are not
    on opposite sides (a pair chosen by hand across the same side has no mirrored numbering)."""
    if {side_a, side_b} != {"L", "R"}:
        return None
    band = _band((ca[1] + cb[1]) / 2.0, height)
    k = _step((abs(ca[0] - axis_x) + abs(cb[0] - axis_x)) / 2.0, axis_x, width)
    left, right = f"{band}{4 - k}", f"{band}{4 + k}"
    return (left, right) if side_a == "L" else (right, left)


def mirror_name(name: str) -> str | None:
    """C3 -> C5, D1b -> D7b; None for a name that is not in the automatic scheme (the artist renamed it)."""
    m = _AUTO_RE.match(name or "")
    if not m:
        return None
    return f"{m[1]}{8 - int(m[2])}{m[3]}"


def suffixes():
    """'', 'b', 'c', … 'z', 'ba', 'bb', … : the order in which a taken name is disambiguated."""
    yield ""
    n = 1
    while True:
        s, k = "", n
        while True:
            s = chr(ord("a") + k % 26) + s
            k //= 26
            if k == 0:
                break
        yield s
        n += 1


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))
