"""Pipeline orchestration: run every step and write a session directory (docs/ARCHITECTURE.md)."""
from __future__ import annotations

from pathlib import Path


def run_pipeline(photo: Path, out_dir: Path, params: dict | None = None, debug: bool = False) -> Path:
    raise NotImplementedError("pipeline work package: implement run_pipeline (see docs/ARCHITECTURE.md)")
