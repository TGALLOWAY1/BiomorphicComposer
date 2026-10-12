"""Debug images for humans: contact sheet of a session (levels, axis, pairs)."""
from __future__ import annotations

from pathlib import Path


def write_debug_sheet(session_dir: Path, out_path: Path | None = None) -> Path:
    raise NotImplementedError("pipeline work package: implement write_debug_sheet")
