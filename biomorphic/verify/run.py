"""Run the whole verification on a sample and write the report (docs/ARCHITECTURE.md, section Verify)."""
from __future__ import annotations

from pathlib import Path


def run_verify(photo: Path, map_png: Path, out_dir: Path, labels_path: Path | None = None,
               working_width: int | None = None, skip_ui: bool = False) -> dict:
    raise NotImplementedError("verify work package: implement run_verify")
