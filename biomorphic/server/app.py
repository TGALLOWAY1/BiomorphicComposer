"""stdlib HTTP server exposing the review API (docs/ARCHITECTURE.md, section Server + UI). Implemented by the UI work package."""
from __future__ import annotations

from pathlib import Path


def serve(session_dir: Path, library_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    raise NotImplementedError("UI work package: implement serve (see docs/ARCHITECTURE.md)")
