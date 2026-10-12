"""Headless drive of the review UI at phone width (Playwright), one screenshot per step.

Steps (docs/V1_BRIEF.md, Verification / Visual):
  01_initial  the page as loaded
  02_slider   after moving #slider to the picked level (value set + `input` and `change` events)
  03_tap      after tapping the region whose twin has the largest IoU; `data-selected`/`data-twins` non-empty
  04_save     after clicking #btn-save (waits for #toast or a new entry in #tray)
  05_tray     the tray with the saved tiles
then the library is opened and the new tile(s) and their provenance are confirmed on disk.

Nothing here raises past `drive_ui`: failures are captured as `ERR_<step>.png` and returned as text.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np

CHROMIUM_FALLBACK = "/opt/pw-browsers/chromium"
VIEWPORT = {"width": 375, "height": 812}
DEVICE_SCALE = 2
STEP_TIMEOUT_MS = 15000
STEPS = ("01_initial", "02_slider", "03_tap", "04_save", "05_tray")

class _NotRun(Exception):
    """The drive cannot run for a reason that is not a UI failure (hooks missing, page not implemented)."""


JS_SELECTED = """() => {
  const pick = (name) => {
    const stage = document.querySelector('#stage');
    if (stage && stage.hasAttribute(name)) return stage.getAttribute(name) || '';
    const any = document.querySelector('[' + name + ']');
    return any ? (any.getAttribute(name) || '') : '';
  };
  return {selected: pick('data-selected'), twins: pick('data-twins')};
}"""

JS_TRAY_COUNT = """() => {
  const tray = document.querySelector('#tray');
  return tray ? tray.children.length : -1;
}"""

JS_TOAST = """() => {
  const t = document.querySelector('#toast');
  if (!t) return '';
  const cs = getComputedStyle(t);
  if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity || '1') === 0) return '';
  return (t.textContent || '').trim();
}"""


# ----------------------------------------------------------------------------- server / browser
def start_server(session_dir: Path | str, library_dir: Path | str, port: int = 0) -> tuple[Any, threading.Thread, int]:
    """Start `biomorphic.server.app.make_server` in a daemon thread; returns (server, thread, port)."""
    from biomorphic.server.app import make_server
    server = make_server(Path(session_dir), Path(library_dir), host="127.0.0.1", port=int(port))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                              name="verify-ui-server", daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def stop_server(server: Any) -> None:
    try:
        server.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        server.server_close()
    except Exception:  # noqa: BLE001
        pass


def launch_browser(playwright: Any) -> Any:
    """Chromium via the server package's helper when it exists, else the installed binary directly."""
    try:
        from biomorphic.server.browser import launch_chromium  # type: ignore[import-not-found]
    except (ImportError, AttributeError):
        launch_chromium = None
    if launch_chromium is not None:
        try:
            return launch_chromium(playwright, headless=True)
        except NotImplementedError:
            pass
    kw: dict[str, Any] = {"headless": True}
    if Path(CHROMIUM_FALLBACK).exists():
        kw["executable_path"] = CHROMIUM_FALLBACK
    return playwright.chromium.launch(**kw)


# ----------------------------------------------------------------------------- choosing what to tap
def tap_target(session: Any, level: int) -> dict:
    """The region to tap at `level`: the member of the highest-IoU mirror pair (else the largest off-axis
    region, else any region) and an interior point of it (distance-transform maximum, in image px)."""
    pairing = session.pairing(level)
    regions = session.regions(level)
    pairs = sorted(pairing.get("pairs", []), key=lambda p: -float(p.get("iou", 0)))
    sides = pairing.get("sides", {})
    if pairs:
        rid, twin, iou = int(pairs[0]["a"]), int(pairs[0]["b"]), float(pairs[0]["iou"])
    else:
        off = [r for r in regions if sides.get(r) != "axis"] or list(regions)
        rid = max(off, key=lambda r: regions[r].area)
        twin, iou = None, None
    lab = session.label_map(level)
    x, y, w, h = regions[rid].bbox
    m = (lab[y:y + h, x:x + w] == rid).astype(np.uint8)
    dist = cv2.distanceTransform(np.pad(m, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    iy, ix = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return {"region_id": rid, "twin_id": twin, "iou": iou, "side": sides.get(rid),
            "image_xy": [int(x + ix), int(y + iy)], "centroid": list(regions[rid].centroid),
            "area": int(regions[rid].area)}


def image_to_page(page: Any, xy: tuple[int, int], image_w: int, image_h: int) -> tuple[float, float]:
    """Map image pixel coords to page coords through #stage's bounding box (the stage shows the whole image)."""
    bb = page.locator("#stage").bounding_box()
    if not bb or bb["width"] <= 0 or bb["height"] <= 0:
        raise RuntimeError("#stage has no bounding box")
    px = bb["x"] + (xy[0] + 0.5) / image_w * bb["width"]
    py = bb["y"] + (xy[1] + 0.5) / image_h * bb["height"]
    return float(px), float(py)


def _settle(page: Any, ms: int = 250) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(ms)


def _shot(page: Any, path: Path) -> str:
    page.screenshot(path=str(path), full_page=False)
    return str(path)


def _library_snapshot(library_dir: Path) -> set[str]:
    try:
        from biomorphic.library.db import Library
        return {str(t.id) for t in Library.open(Path(library_dir)).list_tiles()}
    except Exception:  # noqa: BLE001
        return set()


def confirm_library(library_dir: Path, before: set[str]) -> dict:
    """Open the library and confirm the new tiles have provenance and a PNG on disk."""
    from biomorphic.library.db import Library
    from biomorphic.verify.checks import tile_path
    lib = Library.open(Path(library_dir))
    after = {str(t.id) for t in lib.list_tiles()}
    new = sorted(after - before)
    rows = []
    for tid in new:
        rec = lib.get(tid)
        prov = getattr(rec, "provenance", None)
        if prov is None and isinstance(rec, dict):
            prov = rec.get("provenance")
        has_prov = bool(prov)
        try:
            png = tile_path(lib, tid, library_dir)
            png_ok = png.exists() and png.stat().st_size > 0
        except Exception:  # noqa: BLE001
            png, png_ok = None, False
        rows.append({"id": tid, "provenance_present": has_prov, "png": str(png) if png else None, "png_exists": png_ok,
                     "provenance_keys": sorted(prov.keys()) if isinstance(prov, dict) else None})
    return {"saved_tile_ids": new, "tiles": rows,
            "provenance_ok": bool(new) and all(r["provenance_present"] and r["png_exists"] for r in rows),
            "n_before": len(before), "n_after": len(after)}


# ----------------------------------------------------------------------------- the drive
def drive_ui(session_dir: Path | str, library_dir: Path | str, out_dir: Path | str, port: int = 0,
             level: int | None = None) -> dict:
    """Drive the review UI headlessly; returns {"steps": {step: png}, "saved_tile_ids": [...], "error": ...}."""
    session_dir, library_dir, out_dir = Path(session_dir), Path(library_dir), Path(out_dir)
    ui_dir = out_dir / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"steps": {}, "saved_tile_ids": [], "error": None, "notes": [],
                              "viewport": dict(VIEWPORT), "device_scale_factor": DEVICE_SCALE}
    t0 = time.time()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        result["error"] = f"not run: playwright is not installed ({exc})"
        return result

    try:
        from biomorphic.session import Session
        from biomorphic.verify.checks import pick_level
        session = Session.load(session_dir)
        if level is None:
            level, _ = pick_level(session)
        level = int(level)
        target = tap_target(session, level)
        expected_count = len(session.regions(level))
        result.update({"picked_level": level, "tap_target": target, "expected_count": expected_count})
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"not run: cannot prepare the session for the drive: {type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        return result

    try:
        server, _thread, port = start_server(session_dir, library_dir, port)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"not run: review server unavailable: {type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        return result
    url = f"http://127.0.0.1:{port}/"
    result["url"] = url
    lib_before = _library_snapshot(library_dir)

    step = "00_launch"
    page = None
    browser = None
    try:
        with sync_playwright() as p:
            try:
                browser = launch_browser(p)
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"not run: chromium could not launch: {type(exc).__name__}: {exc}"
                result["traceback"] = traceback.format_exc()
                return result
            try:
                ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=DEVICE_SCALE,
                                          has_touch=True, is_mobile=True)
                page = ctx.new_page()
                page.set_default_timeout(STEP_TIMEOUT_MS)
                console: list[str] = []
                page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

                # 01 initial ------------------------------------------------------------------
                step = "01_initial"
                page.goto(url, wait_until="load")
                _settle(page, 300)
                missing = [sel for sel in ("#stage", "#slider", "#btn-save", "#tray")
                           if page.query_selector(sel) is None]
                if missing:
                    result["steps"][step] = _shot(page, ui_dir / f"{step}.png")
                    raise _NotRun(f"review page at {url} has no {', '.join(missing)} "
                                  "(UI test hooks not implemented yet)")
                page.wait_for_selector("#stage")
                page.wait_for_selector("#slider")
                _settle(page, 500)
                result["steps"][step] = _shot(page, ui_dir / f"{step}.png")

                # 02 slider -------------------------------------------------------------------
                step = "02_slider"
                page.eval_on_selector("#slider", """(el, v) => {
                    el.value = String(v);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""", level)
                try:
                    page.wait_for_function(
                        "(n) => { const c = document.querySelector('#count'); return !!c && (c.textContent || '').includes(String(n)); }",
                        arg=expected_count, timeout=8000)
                except Exception:  # noqa: BLE001
                    result["notes"].append(f"#count did not show {expected_count} within 8 s after moving the slider")
                _settle(page, 500)
                result["slider_value"] = page.eval_on_selector("#slider", "el => el.value")
                result["count_text"] = page.eval_on_selector("#count", "el => el.textContent") if page.query_selector("#count") else None
                result["steps"][step] = _shot(page, ui_dir / f"{step}.png")

                # 03 tap ----------------------------------------------------------------------
                step = "03_tap"
                px, py = image_to_page(page, target["image_xy"], session.width, session.height)
                result["tap_page_xy"] = [round(px, 1), round(py, 1)]
                result["stage_box"] = page.locator("#stage").bounding_box()
                page.mouse.click(px, py)
                sel = _wait_selected(page, 4000)
                if not sel["selected"]:
                    page.touchscreen.tap(px, py)
                    sel = _wait_selected(page, 4000)
                    result["notes"].append("mouse click did not select; used touchscreen.tap")
                _settle(page, 400)
                sel = page.evaluate(JS_SELECTED)
                result["selected"] = sel["selected"]
                result["twins"] = sel["twins"]
                result["steps"][step] = _shot(page, ui_dir / f"{step}.png")
                if not sel["selected"]:
                    raise AssertionError("data-selected is empty after tapping region "
                                         f"{target['region_id']} at page ({px:.0f}, {py:.0f})")
                if target["twin_id"] is not None and not sel["twins"]:
                    raise AssertionError(f"data-twins is empty although region {target['region_id']} has twin "
                                         f"{target['twin_id']} (iou {target['iou']})")
                result["twin_highlighted"] = bool(sel["twins"])

                # 04 save ---------------------------------------------------------------------
                step = "04_save"
                tray_before = page.evaluate(JS_TRAY_COUNT)
                page.click("#btn-save")
                try:
                    page.wait_for_function(
                        "(n) => { const tray = document.querySelector('#tray'); const t = document.querySelector('#toast');"
                        " const trayUp = tray && tray.children.length > n;"
                        " const toastOn = t && (t.textContent || '').trim().length > 0 && getComputedStyle(t).display !== 'none'"
                        " && getComputedStyle(t).visibility !== 'hidden';"
                        " return !!(trayUp || toastOn); }",
                        arg=tray_before, timeout=10000)
                except Exception:  # noqa: BLE001
                    result["notes"].append("neither #toast nor a new #tray entry appeared within 10 s after Save")
                _settle(page, 600)
                result["toast_text"] = page.evaluate(JS_TOAST)
                result["tray_count_before"] = tray_before
                result["tray_count_after"] = page.evaluate(JS_TRAY_COUNT)
                result["steps"][step] = _shot(page, ui_dir / f"{step}.png")

                # 05 tray ---------------------------------------------------------------------
                step = "05_tray"
                tray = page.locator("#tray")
                shot_path = ui_dir / f"{step}.png"
                try:
                    tray.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(200)
                    if tray.is_visible():
                        tray.screenshot(path=str(shot_path))
                    else:
                        page.screenshot(path=str(shot_path), full_page=True)
                except Exception:  # noqa: BLE001
                    page.screenshot(path=str(shot_path), full_page=True)
                result["steps"][step] = str(shot_path)
                result["console"] = console[:50]

                # library confirmation ---------------------------------------------------------
                step = "06_library"
                lib = confirm_library(library_dir, lib_before)
                result.update(lib)
                if not lib["saved_tile_ids"]:
                    raise AssertionError("Save did not add any tile to the library")
                if not lib["provenance_ok"]:
                    raise AssertionError(f"saved tiles lack provenance or PNG: {lib['tiles']}")
            except _NotRun as exc:
                result["error"] = f"not run: {exc}"
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{step}: {type(exc).__name__}: {exc}"
                result["traceback"] = traceback.format_exc()
                if page is not None:
                    try:
                        err_path = ui_dir / f"ERR_{step}.png"
                        page.screenshot(path=str(err_path), full_page=True)
                        result["steps"][f"ERR_{step}"] = str(err_path)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001 - playwright itself failed
        result["error"] = result["error"] or f"{step}: {type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        stop_server(server)
    result["elapsed_s"] = round(time.time() - t0, 2)
    return result


def _wait_selected(page: Any, timeout_ms: int) -> dict:
    try:
        page.wait_for_function(
            "() => { const s = document.querySelector('#stage'); const el = (s && s.hasAttribute('data-selected')) ? s"
            " : document.querySelector('[data-selected]'); return !!(el && (el.getAttribute('data-selected') || '').length); }",
            timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass
    return page.evaluate(JS_SELECTED)
