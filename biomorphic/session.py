"""Shared core: a Session wraps a session directory written by `biomorphic ingest`.

See docs/ARCHITECTURE.md for the on-disk format. Everything downstream (review UI, library, verify)
reads sessions through this module, so the pipeline must write exactly what this module reads.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy import ndimage as ndi

from biomorphic import tiles as tile_utils

GROUP_ID_BASE = 1_000_000
SCHEMA = 1


# ----------------------------------------------------------------------------- merge tree
@dataclass
class MergeTree:
    """Full agglomerative merge hierarchy over the leaf fragments.

    Node ids: 0 = background, 1..n_leaves = leaves, n_leaves+1.. = internal nodes in merge order.
    merges[i] = [a, b, cost, new_id] with new_id == n_leaves + i + 1 and non-decreasing cost.
    """

    n_leaves: int
    merges: list

    @classmethod
    def from_json(cls, d: dict) -> "MergeTree":
        merges = [[int(a), int(b), float(c), int(n)] for a, b, c, n in d["merges"]]
        tree = cls(int(d["n_leaves"]), merges)
        tree.validate()
        return tree

    def to_json(self) -> dict:
        return {"n_leaves": self.n_leaves, "merges": [[a, b, c, n] for a, b, c, n in self.merges]}

    def validate(self) -> None:
        for i, (a, b, c, n) in enumerate(self.merges):
            if n != self.n_leaves + i + 1:
                raise ValueError(f"merge {i}: new_id {n} != {self.n_leaves + i + 1}")
            if not (0 < a < n and 0 < b < n and a != b):
                raise ValueError(f"merge {i}: bad children {a}, {b}")
            if i and c < self.merges[i - 1][2] - 1e-9:
                raise ValueError(f"merge {i}: cost decreases")

    @property
    def n_nodes(self) -> int:
        return self.n_leaves + len(self.merges) + 1

    def roots(self, n_merges: int) -> np.ndarray:
        """root[node] after applying the first n_merges merges (root[0] == 0)."""
        n_merges = max(0, min(int(n_merges), len(self.merges)))
        parent = np.arange(self.n_nodes, dtype=np.int64)
        for a, b, _c, new in self.merges[:n_merges]:
            parent[a] = new
            parent[b] = new
        root = parent.copy()
        for i in range(self.n_nodes - 1, 0, -1):  # parents have larger ids, so they are resolved first
            p = parent[i]
            if p != i:
                root[i] = root[p]
        return root

    def cost_at(self, n_merges: int) -> float:
        if n_merges <= 0:
            return 0.0
        return float(self.merges[min(n_merges, len(self.merges)) - 1][2])

    def n_merges_for_regions(self, n_regions: int) -> int:
        """Number of merges that leaves exactly n_regions regions (clamped)."""
        return max(0, min(len(self.merges), self.n_leaves - int(n_regions)))

    def children(self) -> dict[int, tuple[int, int]]:
        return {n: (a, b) for a, b, _c, n in self.merges}

    def leaves_under(self, node: int) -> list[int]:
        ch = self.children()
        out, stack = [], [int(node)]
        while stack:
            k = stack.pop()
            if k in ch:
                stack.extend(ch[k])
            else:
                out.append(k)
        return sorted(out)


# ----------------------------------------------------------------------------- regions
@dataclass
class RegionInfo:
    id: int
    area: int
    bbox: tuple[int, int, int, int]        # x, y, w, h in working px
    centroid: tuple[float, float]          # cx, cy
    mean_lab: tuple[float, float, float]
    leaves: tuple[int, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "area": self.area, "bbox": list(self.bbox),
            "centroid": [round(self.centroid[0], 2), round(self.centroid[1], 2)],
            "mean_lab": [round(v, 2) for v in self.mean_lab], "leaves": list(self.leaves),
        }


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """uint8 RGB -> float32 CIE Lab with L in 0..100 and a, b centred on 0."""
    f = rgb.astype(np.float32) / 255.0
    return cv2.cvtColor(f, cv2.COLOR_RGB2LAB)


# ----------------------------------------------------------------------------- session
class Session:
    """Read side of a session directory plus the UI working state (manual groups)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.meta = json.loads((self.path / "session.json").read_text())
        if self.meta.get("schema") != SCHEMA:
            raise ValueError(f"unsupported session schema {self.meta.get('schema')!r}")
        self.tree = MergeTree.from_json(json.loads((self.path / "merge_tree.json").read_text()))
        self.fragments = np.load(self.path / "fragments.npy").astype(np.int32)
        bgr = cv2.imread(str(self.path / "working.png"), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(self.path / "working.png")
        self.working_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self.figure_mask = self._read_mask("figure_mask.png")
        self.ink_mask = self._read_mask("ink_mask.png")
        if self.fragments.shape != self.working_rgb.shape[:2]:
            raise ValueError("fragments.npy and working.png disagree on size")
        state_path = self.path / "state.json"
        self.state: dict[str, Any] = json.loads(state_path.read_text()) if state_path.exists() else {"groups": []}
        self.state.setdefault("groups", [])
        self._lab: np.ndarray | None = None
        self._roots: dict[int, np.ndarray] = {}
        self._levels_cache: dict[tuple, dict[str, Any]] = {}

    @classmethod
    def load(cls, path: Path | str) -> "Session":
        return cls(path)

    def _read_mask(self, name: str) -> np.ndarray:
        m = cv2.imread(str(self.path / name), cv2.IMREAD_GRAYSCALE)
        if m is None:
            raise FileNotFoundError(self.path / name)
        return m > 127

    # ---- metadata -----------------------------------------------------------------------
    @property
    def session_id(self) -> str:
        return str(self.meta["session_id"])

    @property
    def levels(self) -> list[dict[str, Any]]:
        return list(self.meta["levels"])

    @property
    def axis_x(self) -> float:
        return float(self.meta["axis"]["x"])

    @property
    def width(self) -> int:
        return int(self.fragments.shape[1])

    @property
    def height(self) -> int:
        return int(self.fragments.shape[0])

    @property
    def working_lab(self) -> np.ndarray:
        if self._lab is None:
            self._lab = rgb_to_lab(self.working_rgb)
        return self._lab

    @property
    def groups(self) -> list[list[int]]:
        return [list(map(int, g)) for g in self.state["groups"]]

    def _groups_key(self) -> tuple:
        return tuple(tuple(sorted(g)) for g in self.groups)

    def level_n_merges(self, level_index: int) -> int:
        levels = self.levels
        if not 0 <= level_index < len(levels):
            raise IndexError(f"level {level_index} out of range 0..{len(levels) - 1}")
        return int(levels[level_index]["n_merges"])

    # ---- label maps ---------------------------------------------------------------------
    def roots(self, n_merges: int) -> np.ndarray:
        if n_merges not in self._roots:
            self._roots[n_merges] = self.tree.roots(n_merges)
        return self._roots[n_merges]

    def _level_data(self, level_index: int, apply_groups: bool = True) -> dict[str, Any]:
        key = (level_index, self._groups_key() if apply_groups else ())
        if key in self._levels_cache:
            return self._levels_cache[key]
        n_merges = self.level_n_merges(level_index)
        root = self.roots(n_merges)
        remap = root.copy()
        if apply_groups and self.groups:
            remap = self._apply_groups(remap)
        label_map = remap[self.fragments].astype(np.int32)
        leaf_to_region = remap[: self.tree.n_leaves + 1].astype(np.int64)  # index = leaf id
        data = {"label_map": label_map, "leaf_to_region": leaf_to_region}
        self._levels_cache[key] = data
        return data

    def _apply_groups(self, remap: np.ndarray) -> np.ndarray:
        """Union every region touched by a manual group into the group's region (id GROUP_ID_BASE + index)."""
        groups = self.groups
        parent = list(range(len(groups)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                lo, hi = min(ri, rj), max(ri, rj)
                parent[hi] = lo  # smaller index is the representative

        root_to_group: dict[int, int] = {}
        for gi, leaves in enumerate(groups):
            for leaf in leaves:
                r = int(remap[leaf])
                if r in root_to_group:
                    union(gi, root_to_group[r])
                else:
                    root_to_group[r] = gi
        out = remap.copy()
        for r, gi in root_to_group.items():
            out[out == r] = GROUP_ID_BASE + find(gi)
        return out

    def label_map(self, level_index: int, apply_groups: bool = True) -> np.ndarray:
        """int32 HxW region ids (0 = background). Groups applied unless apply_groups=False."""
        return self._level_data(level_index, apply_groups)["label_map"]

    def leaf_to_region(self, level_index: int) -> np.ndarray:
        """Array indexed by leaf id giving the region id at this level (index 0 unused)."""
        return self._level_data(level_index)["leaf_to_region"]

    def region_leaves(self, level_index: int, region_id: int) -> list[int]:
        l2r = self.leaf_to_region(level_index)
        return [int(i) for i in np.nonzero(l2r == region_id)[0] if i != 0]

    # ---- region stats -------------------------------------------------------------------
    def regions(self, level_index: int) -> dict[int, RegionInfo]:
        data = self._level_data(level_index)
        if "regions" in data:
            return data["regions"]
        lab = data["label_map"]
        ids, inv = np.unique(lab, return_inverse=True)
        dense = inv.reshape(lab.shape).astype(np.int32)
        k = len(ids)
        flat = dense.ravel()
        area = np.bincount(flat, minlength=k)
        h, w = lab.shape
        xs = np.tile(np.arange(w, dtype=np.float64), h)
        ys = np.repeat(np.arange(h, dtype=np.float64), w)
        sx = np.bincount(flat, weights=xs, minlength=k)
        sy = np.bincount(flat, weights=ys, minlength=k)
        labf = self.working_lab.reshape(-1, 3)
        means = [np.bincount(flat, weights=labf[:, c].astype(np.float64), minlength=k) for c in range(3)]
        bg = int(np.searchsorted(ids, 0)) if ids[0] == 0 else -1
        # find_objects wants labels >= 1; shift dense so that background (if any) becomes 0
        shifted = dense + 1 if bg < 0 else np.where(dense == bg, 0, np.where(dense > bg, dense, dense + 1))
        objs = ndi.find_objects(shifted)
        l2r = data["leaf_to_region"]
        leaves_by_region: dict[int, list[int]] = {}
        for leaf in range(1, len(l2r)):
            leaves_by_region.setdefault(int(l2r[leaf]), []).append(leaf)
        regions: dict[int, RegionInfo] = {}
        for di, rid in enumerate(ids.tolist()):
            if rid == 0:
                continue
            a = int(area[di])
            if a == 0:
                continue
            v = di if bg < 0 else (di if di < bg else di - 1)
            sl = objs[v]
            bbox = (int(sl[1].start), int(sl[0].start), int(sl[1].stop - sl[1].start), int(sl[0].stop - sl[0].start))
            regions[int(rid)] = RegionInfo(
                id=int(rid), area=a, bbox=bbox,
                centroid=(float(sx[di] / a), float(sy[di] / a)),
                mean_lab=tuple(float(m[di] / a) for m in means),
                leaves=tuple(leaves_by_region.get(int(rid), ())),
            )
        data["regions"] = regions
        return regions

    # ---- pairing ------------------------------------------------------------------------
    def pairing(self, level_index: int) -> dict[str, Any]:
        data = self._level_data(level_index)
        if "pairing" not in data:
            from biomorphic.pipeline import pairing as pairing_mod
            data["pairing"] = pairing_mod.pair_regions(
                data["label_map"], self.axis_x, self.working_lab, self.regions(level_index),
                params=self.meta.get("params", {}))
        return data["pairing"]

    # ---- manual groups ------------------------------------------------------------------
    def add_group(self, leaf_ids: list[int]) -> int:
        leaves = sorted({int(i) for i in leaf_ids if 0 < int(i) <= self.tree.n_leaves})
        if len(leaves) < 1:
            raise ValueError("a group needs at least one leaf")
        self.state["groups"].append(leaves)
        self._levels_cache = {k: v for k, v in self._levels_cache.items() if k[1] == ()}
        self.save_state()
        return len(self.state["groups"]) - 1

    def remove_group(self, group_index: int) -> None:
        del self.state["groups"][group_index]
        self._levels_cache = {k: v for k, v in self._levels_cache.items() if k[1] == ()}
        self.save_state()

    def save_state(self) -> None:
        (self.path / "state.json").write_text(json.dumps(self.state, indent=1))

    # ---- tiles --------------------------------------------------------------------------
    def cut_tile(self, level_index: int, region_id: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        info = self.regions(level_index).get(int(region_id))
        if info is None:
            raise KeyError(f"no region {region_id} at level {level_index}")
        return tile_utils.cut_tile(self.working_rgb, self.label_map(level_index), int(region_id), info.bbox)


# ----------------------------------------------------------------------------- writing helpers
def make_session_id(source_sha256: str, pipeline_version: str, created_at: str) -> str:
    return hashlib.sha256(f"{source_sha256}{pipeline_version}{created_at}".encode()).hexdigest()[:8]


def write_session(out_dir: Path | str, *, meta: dict, working_rgb: np.ndarray, figure_mask: np.ndarray,
                  ink_mask: np.ndarray, fragments: np.ndarray, tree: MergeTree) -> Path:
    """Write a complete session directory. The pipeline calls this; tests use it to build fixtures."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tree.validate()
    if fragments.shape != working_rgb.shape[:2]:
        raise ValueError("fragments and working copy sizes differ")
    if int(fragments.max()) > tree.n_leaves:
        raise ValueError("fragment id exceeds n_leaves")
    cv2.imwrite(str(out / "working.png"), cv2.cvtColor(working_rgb, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out / "figure_mask.png"), (figure_mask.astype(np.uint8) * 255))
    cv2.imwrite(str(out / "ink_mask.png"), (ink_mask.astype(np.uint8) * 255))
    np.save(out / "fragments.npy", fragments.astype(np.int32))
    (out / "merge_tree.json").write_text(json.dumps(tree.to_json()))
    meta = dict(meta)
    meta["schema"] = SCHEMA
    (out / "session.json").write_text(json.dumps(meta, indent=1))
    return out
