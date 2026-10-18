"""Drive the review page headlessly at phone width (375 x 812, touch) against the synthetic session.

Screenshots of every step land in $BIOMORPHIC_SHOTS_DIR when set, else in pytest's tmp dir (printed at the end).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from biomorphic.server.app import make_server
from biomorphic.session import GROUP_ID_BASE
from biomorphic.testing import H, RECTS, W, synthetic_session

pw = pytest.importorskip("playwright.sync_api")
from biomorphic.server.browser import launch_chromium  # noqa: E402

VIEWPORT = {"width": 375, "height": 812}


def centre(region_id: int) -> tuple[float, float]:
    x0, y0, x1, y1, _ = RECTS[region_id]
    return (x0 + x1) / 2, (y0 + y1) / 2


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    srv = make_server(synthetic_session(root / "session"), root / "library", "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = launch_chromium(p)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        try:
            yield b
        finally:
            b.close()


@pytest.fixture(autouse=True)
def fresh_groups(server):
    """Each test starts from the session without manual groups, whatever an earlier test left behind."""
    state = server.state
    with state.lock:
        while state.session.groups:
            state.session.remove_group(len(state.session.groups) - 1)
        state._png_cache.clear()
    yield


@pytest.fixture
def shots_dir(tmp_path: Path) -> Path:
    d = Path(os.environ.get("BIOMORPHIC_SHOTS_DIR") or tmp_path / "ui-shots")
    d.mkdir(parents=True, exist_ok=True)
    return d


class Page:
    """Thin wrapper: open the page, tap in image coordinates, read the hooks."""

    def __init__(self, browser, url: str, shots_dir: Path):
        self.ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2, is_mobile=True, has_touch=True)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(15000)
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        self.shots_dir = shots_dir
        self.page.goto(url, wait_until="load")
        self.page.wait_for_selector('#stage[data-ready="1"][data-level="0"]')

    def close(self) -> None:
        self.ctx.close()

    def shot(self, name: str) -> Path:
        path = self.shots_dir / f"{name}.png"
        self.page.screenshot(path=str(path))
        return path

    def tap(self, ix: float, iy: float) -> None:
        box = self.page.locator("#stage").bounding_box()
        assert box and box["width"] > 0
        self.page.touchscreen.tap(box["x"] + ix / W * box["width"], box["y"] + iy / H * box["height"])

    def attr(self, name: str) -> list[int]:
        raw = self.page.get_attribute("#stage", name) or ""
        return [int(v) for v in raw.split(",") if v]

    def count(self) -> int:
        return int(self.page.inner_text("#count").strip())

    def wait_level(self, i: int) -> None:
        self.page.wait_for_selector(f'#stage[data-level="{i}"]')

    def wait_count(self, n: int) -> None:
        self.page.wait_for_function("n => document.querySelector('#count').textContent.trim() === String(n)", arg=n)


def test_review_flow_on_a_phone(server, browser, shots_dir):
    ui = Page(browser, server.url, shots_dir)
    try:
        page = ui.page
        assert ui.count() == 5
        assert page.inner_text("#hdr-file") == "synthetic.png" and page.inner_text("#hdr-axis") == "120"
        assert page.is_checked("#mirror-merges")
        assert page.is_disabled("#btn-merge") and page.is_disabled("#btn-save")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")   # no horizontal scroll
        ui.shot("01-loaded")

        # the slider: keyboard moves it, the count and the cost follow the level
        page.focus("#slider")
        page.keyboard.press("ArrowRight")
        ui.wait_level(1)
        assert ui.count() == 3 and page.input_value("#slider") == "1"
        assert page.inner_text("#cost") == "0.50"
        ui.shot("02-level-1")
        page.keyboard.press("ArrowLeft")
        ui.wait_level(0)
        assert ui.count() == 5

        # tap region 1: selected, twin 3 co-selected
        ui.tap(*centre(1))
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '1'")
        assert ui.attr("data-selected") == [1] and ui.attr("data-twins") == [3]
        assert not page.is_disabled("#btn-save") and page.is_disabled("#btn-merge")
        assert "IoU" in page.inner_text("#info")
        ui.shot("03-tapped-1-twin-3")

        # selection survives a level change when the id survives (1 -> level 1 merges it away, 5 survives)
        ui.tap(*centre(5))
        assert ui.attr("data-selected") == [1, 5] and ui.attr("data-twins") == [3]
        page.focus("#slider")
        page.keyboard.press("ArrowRight")
        ui.wait_level(1)
        assert ui.attr("data-selected") == [5] and ui.attr("data-twins") == []
        page.keyboard.press("ArrowLeft")
        ui.wait_level(0)

        # tapping again deselects (and the twin goes too); tapping the twin of a selection drops the pair
        page.keyboard.press("Escape")
        assert ui.attr("data-selected") == [] and ui.attr("data-twins") == []
        ui.tap(*centre(1))
        ui.tap(*centre(1))
        assert ui.attr("data-selected") == [] and ui.attr("data-twins") == []
        ui.tap(*centre(1))
        ui.tap(*centre(3))                      # the orange twin
        assert ui.attr("data-selected") == [] and ui.attr("data-twins") == []
        ui.shot("04-deselected")

        # two left regions + Merge (mirror on): group 0 = {1,2}, group 1 = {3,4}; 5 regions -> 3
        ui.tap(*centre(1))
        ui.tap(*centre(2))
        assert ui.attr("data-selected") == [1, 2] and ui.attr("data-twins") == [3, 4]
        assert not page.is_disabled("#btn-merge")
        page.click("#btn-merge")
        ui.wait_count(3)
        ui.wait_level(0)
        assert server.state.session.groups == [[1, 2], [3, 4]]
        assert ui.attr("data-selected") == [GROUP_ID_BASE] and ui.attr("data-twins") == [GROUP_ID_BASE + 1]
        assert ui.attr("data-selected")[0] == page.evaluate("Number(document.querySelector('#stage').dataset.selected)")
        assert not page.is_hidden("#btn-unmerge")
        ui.shot("05-merged")

        # Save the merged pair (when the library is available)
        if server.state.library is None:
            page.click("#btn-save")
            page.wait_for_selector('#toast[data-show="1"]')
            assert "library" in page.inner_text("#toast").lower()
            ui.shot("06-save-unavailable")
        else:
            page.click("#btn-save")
            page.wait_for_function("document.querySelector('#toast').textContent.startsWith('Saved 2')")
            # two composites, each stored with its two component tiles: six tiles in the tray
            page.wait_for_function("document.querySelectorAll('#tray .tile').length === 6")
            ids = page.eval_on_selector_all("#tray .tile", "els => els.map(e => e.dataset.id)")
            assert sorted(ids) == sorted(t.id for t in server.state.library.list_tiles())
            composites = [i for i in ids if i.endswith(f"-L00-r{GROUP_ID_BASE}") or i.endswith(f"-L00-r{GROUP_ID_BASE + 1}")]
            assert len(composites) == 2 and len(ids) == 6
            assert page.locator("#tray .tile .badge.pair").count() == 2
            assert page.locator("#tray .tile .badge.composite").count() == 2
            assert "2 parts" in page.inner_text("#tray .tile .badge.composite >> nth=0")
            page.locator("#tray").scroll_into_view_if_needed()          # thumbnails load lazily
            page.wait_for_function("[...document.querySelectorAll('#tray .tile img')].every(i => i.complete && i.naturalWidth > 0)")
            assert "6 tiles in library" in page.inner_text("#hdr-lib")
            page.wait_for_function("document.querySelector('#info').textContent.includes('saved')")
            ui.shot("06-saved")

        # Unmerge puts the five cells back; Clear empties the selection
        page.click("#btn-unmerge")
        ui.wait_count(5)
        assert server.state.session.groups == []
        ui.tap(*centre(2))
        assert ui.attr("data-selected") == [2]
        page.click("#btn-clear")
        assert ui.attr("data-selected") == [] and ui.attr("data-twins") == []
        ui.shot("07-unmerged-cleared")
        assert ui.errors == []
        print(f"\nscreenshots: {shots_dir}")
    finally:
        ui.close()


def test_dark_theme_and_tablet_width(server, browser, shots_dir):
    ctx = browser.new_context(viewport={"width": 820, "height": 1180}, device_scale_factor=2, color_scheme="dark")
    page = ctx.new_page()
    try:
        page.goto(server.url, wait_until="load")
        page.wait_for_selector('#stage[data-ready="1"]')
        box = page.locator("#stage").bounding_box()
        assert box["width"] <= 760 and box["width"] > 600
        page.mouse.click(box["x"] + centre(2)[0] / W * box["width"], box["y"] + centre(2)[1] / H * box["height"])
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '2'")
        assert page.get_attribute("#stage", "data-twins") == "4"
        page.screenshot(path=str(shots_dir / "08-tablet-dark.png"))
    finally:
        ctx.close()


def test_tap_recovers_after_a_press_released_off_the_canvas(server, browser, shots_dir):
    """A mouse press that ends outside the canvas (mouse and pen have no implicit capture) must not swallow
    every later tap."""
    ui = Page(browser, server.url, shots_dir)
    try:
        page = ui.page
        box = page.locator("#stage").bounding_box()
        cx, cy = centre(1)
        x, y = box["x"] + cx / W * box["width"], box["y"] + cy / H * box["height"]
        page.uncheck("#paint-mode")                 # a drag is a scroll, not a stroke
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(box["x"] + 10, max(1.0, box["y"] - 30), steps=5)   # drag out of the stage, into the header
        page.mouse.up()
        assert ui.attr("data-selected") == []
        ui.tap(cx, cy)
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '1'")
        assert ui.attr("data-selected") == [1] and ui.attr("data-twins") == [3]
        page.mouse.click(x, y)                      # a plain mouse click still toggles
        page.wait_for_function("document.querySelector('#stage').dataset.selected === ''")
        # with paint on, the same drag is a stroke that selects the part it started on, and taps still work after
        page.check("#paint-mode")
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(box["x"] + 10, max(1.0, box["y"] - 30), steps=5)
        page.mouse.up()
        assert ui.attr("data-selected") == [1]
        ui.tap(cx, cy)
        page.wait_for_function("document.querySelector('#stage').dataset.selected === ''")
        assert not ui.errors, ui.errors
    finally:
        ui.close()


def test_paint_a_stroke_across_parts_selects_them(server, browser, shots_dir):
    """Paint mode (on by default): a drag selects every part under the stroke, twins follow, a tap removes one
    part again, and with paint off a drag selects nothing (the browser may scroll instead)."""
    ui = Page(browser, server.url, shots_dir)
    try:
        page = ui.page
        assert page.is_checked("#paint-mode")
        assert page.get_attribute("#stage-wrap", "data-paint") == "1"
        assert "Drag" in page.inner_text("#info")
        box = page.locator("#stage").bounding_box()
        to_page = lambda ix, iy: (box["x"] + ix / W * box["width"], box["y"] + iy / H * box["height"])  # noqa: E731
        # a stroke from cell 1 down through cell 2 (they touch at y = 90..100 across an ink line)
        x1, y1 = to_page(*centre(1))
        x2, y2 = to_page(*centre(2))
        page.mouse.move(x1, y1)
        page.mouse.down()
        page.mouse.move(x2, y2, steps=12)
        page.mouse.up()
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '1,2'")
        assert ui.attr("data-selected") == [1, 2] and ui.attr("data-twins") == [3, 4]
        assert not page.is_disabled("#btn-merge")
        ui.shot("paint-01-stroke")
        # the stroke's end point is not toggled off: a second stroke over the same parts changes nothing
        page.mouse.move(x2, y2); page.mouse.down(); page.mouse.move(x1, y1, steps=12); page.mouse.up()
        assert ui.attr("data-selected") == [1, 2]
        # a tap removes an overshoot
        ui.tap(*centre(2))
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '1'")
        assert ui.attr("data-twins") == [3]
        # a stroke across the axis: 1 -> the axis cell 5 -> 3; painting over the orange twin selects both sides
        page.mouse.move(x1, y1); page.mouse.down()
        page.mouse.move(*to_page(*centre(3)), steps=12); page.mouse.up()
        assert sorted(ui.attr("data-selected")) == [1, 3, 5] and ui.attr("data-twins") == []
        page.click("#btn-clear")
        # paint off: the same drag selects nothing, a tap still does
        page.uncheck("#paint-mode")
        assert page.get_attribute("#stage-wrap", "data-paint") == ""
        page.mouse.move(x1, y1); page.mouse.down(); page.mouse.move(x2, y2, steps=12); page.mouse.up()
        assert ui.attr("data-selected") == []
        ui.tap(*centre(1))
        assert ui.attr("data-selected") == [1]
        page.check("#paint-mode")
        ui.shot("paint-02-off-then-on")
        assert ui.errors == []
    finally:
        ui.close()
