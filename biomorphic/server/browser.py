"""Launch a headless Chromium through Playwright on machines where the default install is incomplete.

The default `playwright.chromium.launch()` wants the headless shell that matches the installed
Playwright package; on this box only the full Chromium build is present. `launch_chromium` tries,
in order: the default launch, the executable named by `PLAYWRIGHT_CHROMIUM_EXECUTABLE`, and the
conventional `/opt/pw-browsers/chromium` symlink. Both the UI tests and `biomorphic verify` use it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

FALLBACK_EXECUTABLES = ("/opt/pw-browsers/chromium",)


def chromium_candidates() -> list[str]:
    """Executable paths to try after the default launch, in order, existing files only."""
    out: list[str] = []
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    for candidate in (env, *FALLBACK_EXECUTABLES):
        if candidate and Path(candidate).exists() and candidate not in out:
            out.append(candidate)
    return out


def launch_chromium(playwright: Any, **kw: Any):
    """Return a launched Playwright Browser (Chromium). `kw` goes to `chromium.launch` (headless by default).

    Raises RuntimeError with every attempt's error when nothing can be launched.
    """
    kw = dict(kw)
    kw.setdefault("headless", True)
    errors: list[str] = []
    try:
        return playwright.chromium.launch(**kw)
    except Exception as exc:  # noqa: BLE001 - we report every attempt below
        errors.append(f"default launch: {str(exc).splitlines()[0]}")
    if "executable_path" not in kw:
        for exe in chromium_candidates():
            try:
                return playwright.chromium.launch(executable_path=exe, **kw)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{exe}: {str(exc).splitlines()[0]}")
    raise RuntimeError("could not launch Chromium (set PLAYWRIGHT_CHROMIUM_EXECUTABLE): " + "; ".join(errors))
