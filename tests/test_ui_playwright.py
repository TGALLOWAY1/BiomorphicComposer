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
    """Each test starts from the session without manual groups and from an empty library, whatever an earlier
    test left behind."""
    state = server.state
    with state.lock:
        while state.session.groups:
            state.session.remove_group(len(state.session.groups) - 1)
        if state.library is not None:
            for t in state.library.list_tiles():
                state.library.delete_tile(t.id)
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
        self.page.goto(url + "#review", wait_until="load")      # the stage the artist works in; the page opens on Capture
        self.page.wait_for_selector('#stage[data-ready="1"][data-level="0"]')

    def close(self) -> None:
        self.ctx.close()

    def shot(self, name: str) -> Path:
        path = self.shots_dir / f"{name}.png"
        self.page.screenshot(path=str(path))
        return path

    def tap(self, ix: float, iy: float) -> None:
        self.page.evaluate("window.scrollTo(0, 0)")             # the tray is below the drawing; a tap needs it on screen
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
        assert "IoU" in page.text_content("#info") and "mirror twin, auto-selected" in page.text_content("#info")
        assert page.inner_text("#btn-save") == "Save pair to tray"
        assert "IoU" in page.inner_text("#mirror-tag")              # the tag on the axis between the two
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
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")   # Unmerge joined the row
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
            # the tray shows names, not ids: a mirrored pair of names for the two composites
            names = page.eval_on_selector_all("#tray .tile .tile-id", "els => els.map(e => e.textContent)")
            assert len(set(names)) == 6 and sum(" · mirror" in n for n in names) == 1
            # a save ends the selection (the cut-outs have gone to the tray); the saved parts say so when tapped
            page.wait_for_function("document.querySelector('#stage').dataset.selected === ''")
            assert page.is_disabled("#btn-save")
            ui.shot("06-saved")
        ui.tap(*centre(1))                      # inside the left composite
        page.wait_for_function(f"document.querySelector('#stage').dataset.selected === '{GROUP_ID_BASE}'")
        if server.state.library is not None:
            page.wait_for_function("document.querySelector('#info').textContent.includes('saved as')")

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
        page.goto(server.url + "#review", wait_until="load")
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
        # (through the middle of 5: the straight line from 1 to 3 runs along 5's top edge, a pixel either way)
        page.mouse.move(x1, y1); page.mouse.down()
        page.mouse.move(*to_page(*centre(5)), steps=8)
        page.mouse.move(*to_page(*centre(3)), steps=8); page.mouse.up()
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


def _open(browser, url: str, viewport: dict, **kw):
    ctx = browser.new_context(viewport=viewport, device_scale_factor=1, **kw)
    page = ctx.new_page()
    page.set_default_timeout(15000)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.goto(url, wait_until="load")
    page.wait_for_selector('#stage[data-ready="1"]', state="attached")   # hidden on the library stage
    return ctx, page, errors


def test_the_eight_stages_walk(server, browser, shots_dir):
    """The page is the reel's stage-by-stage flow: it opens on Capture, each stage shows its own card with the
    session's real numbers, a tap on the merge screen starts a review, and the two unbuilt stages say so."""
    ctx, page, errors = _open(browser, server.url, {"width": 1280, "height": 860})
    try:
        current = lambda: page.get_attribute("body", "data-ch")                       # noqa: E731
        visible = lambda sel: page.locator(sel).is_visible()                          # noqa: E731
        assert current() == "1" and page.locator("#rail .chap").count() == 8
        assert page.inner_text("#cap-t") == "Capture" and page.inner_text("#cap-n") == "01 / 08"
        assert page.inner_text("#cap-file") == "synthetic.png"
        steps = page.locator("#cap-steps .step")
        assert steps.count() == 4 and "figure box 200 × 140 px" in steps.nth(0).inner_text()
        assert "240 × 200 px" in steps.nth(2).inner_text()
        page.wait_for_function("document.querySelectorAll('#cap-steps .step.on').length === 4")   # the checks tick in
        assert not visible("#slider") and not visible("#btn-save")
        page.screenshot(path=str(shots_dir / "stage-1-capture.png"))

        page.click("#next")                                    # 02: the search curve, the axis and the match
        assert current() == "2" and page.inner_text("#cap-t") == "Find the mirror axis"
        page.wait_for_selector("#axis-chart polyline")
        assert page.inner_text("#ax-x") == "120 px" and page.inner_text("#ax-r") == "1.00"
        page.wait_for_function("document.querySelector('#axis-tag').textContent === 'axis x = 120 px'")
        page.click("#btn-fold")
        assert page.get_attribute("#btn-fold", "aria-pressed") == "true"
        page.screenshot(path=str(shots_dir / "stage-2-axis.png"))

        page.click('#rail .chap[data-ch="3"]')                 # 03: the fragment count
        page.wait_for_function("document.querySelector('#frag-count').textContent === '5'")
        assert "too many pieces" in page.inner_text("#split-kv")
        with server.state.lock:                                # composites made later do not change what the split found
            server.state.merge(0, [1, 2], True)
        page.click("#btn-replay")
        page.wait_for_function("document.querySelector('#frag-count').textContent === '5'")
        with server.state.lock:
            server.state.unmerge(1); server.state.unmerge(0)
        page.screenshot(path=str(shots_dir / "stage-3-split.png"))

        page.click('#rail .chap[data-ch="4"]')                 # 04: the slider and the real merge tree
        assert visible("#slider") and not visible("#btn-save")
        page.wait_for_function("document.querySelectorAll('#dendro path').length === 4")          # one branch per merge
        assert page.locator("#ticks span").all_inner_texts() == ["5", "3", "2", "1"]
        cut0 = page.get_attribute("#dendro line[stroke-dasharray]", "y1")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector('#stage[data-level="1"]')
        assert page.get_attribute("#dendro line[stroke-dasharray]", "y1") != cut0                 # the cut line moved
        page.keyboard.press("ArrowLeft")
        page.wait_for_selector('#stage[data-level="0"]')
        page.screenshot(path=str(shots_dir / "stage-4-merge.png"))

        box = page.locator("#stage").bounding_box()            # a tap here is the start of a review
        page.mouse.click(box["x"] + centre(1)[0] / W * box["width"], box["y"] + centre(1)[1] / H * box["height"])
        page.wait_for_function("document.querySelector('#stage').dataset.selected === '1'")
        assert current() == "5" and visible("#btn-save") and page.url.endswith("#review")
        assert box["y"] + box["height"] <= 860                 # the whole print fits the window beside the cards
        page.click("#btn-save")
        page.wait_for_function("document.querySelectorAll('#tray .tile').length === 2")
        page.screenshot(path=str(shots_dir / "stage-5-review.png"))

        for ch, title in (("7", "Compose"), ("8", "Recolor and export")):                         # shown, not built
            page.click(f'#rail .chap[data-ch="{ch}"]')
            assert page.inner_text("#cap-t") == title and not visible("#stage") and not visible("#next")
            assert "not built" in page.locator(f'.card[data-chs="{ch}"]').inner_text().lower()
        assert errors == []
    finally:
        ctx.close()


def test_library_modes_search_tags_and_rename(server, browser, shots_dir):
    state = server.state
    with state.lock:
        state.save(0, [1, 3])
        state.save(0, [5])
    ctx, page, errors = _open(browser, server.url + "#library", {"width": 1280, "height": 860})
    try:
        page.wait_for_function("document.querySelectorAll('#lib-grid .lcard').length >= 3")
        names = page.locator("#lib-grid .lcard .id").all_inner_texts()
        assert names == sorted(names) and len(set(names)) == len(names)
        assert "parts approved" in page.inner_text("#sources") and "in library" in page.inner_text("#sources")
        assert page.locator("#lib-grid .lcard .pr.axis").count() >= 1                 # the axis part has no pair
        assert page.locator("#lib-grid .lcard .pv").first.inner_text().strip().startswith("synthetic")

        # render modes are views: only the picture's query changes
        first = page.locator("#lib-grid .lcard .pic img").first
        assert "mode=" not in first.get_attribute("src")
        page.click('#modes [data-mode="outline"]')
        page.wait_for_function("document.querySelector('#lib-grid .pic img').src.endsWith('?mode=outline')")
        page.click('#modes [data-mode="remap"]')
        page.wait_for_function("document.querySelector('#lib-grid .pic img').src.endsWith('?mode=remap')")
        assert page.locator("#palette .sw span").count() == 8 and page.locator("#palette").is_visible()
        page.click('#modes [data-mode="original"]')
        assert not page.locator("#palette").is_visible()

        # the sheet: tag a part, rename it
        card = page.locator("#lib-grid .lcard").first
        tile_id = card.get_attribute("data-id")
        card.click()
        page.wait_for_selector("#sheet[open]")
        assert tile_id in page.inner_text("#sheet") and "synthetic.png" in page.inner_text("#sheet")
        page.fill("#sheet-tag-input", "wing")
        page.press("#sheet-tag-input", "Enter")
        page.wait_for_selector("#sheet-tags .chip.on")
        assert state.library.tags(tile_id) == ["wing"]
        page.fill("#sheet-name", "Crest")
        page.click("#sheet-rename")
        page.wait_for_function("document.querySelector('#toast').textContent === 'Renamed to Crest'")
        assert state.library.get(tile_id).name == "Crest"
        # a tag added after the rename (which refetched the library under the open sheet) still reaches the grid
        page.fill("#sheet-tag-input", "outer")
        page.press("#sheet-tag-input", "Enter")
        page.wait_for_function("document.querySelectorAll('#sheet-tags .chip.on').length === 2")
        assert page.locator(f'#lib-grid .lcard[data-id="{tile_id}"] .tagc').inner_text() == "#outer +1"
        page.click("#sheet-close")
        page.wait_for_function("!document.querySelector('#sheet').open")

        # search dims everything that does not match and marks what does
        page.fill("#lib-search", "wing")
        page.wait_for_function("document.querySelectorAll('#lib-grid .lcard.hit').length === 1")
        assert page.locator("#lib-grid .lcard.hit .id").inner_text() == "Crest"
        assert page.locator("#lib-grid .lcard.hit .tagc").inner_text() == "#wing"
        assert page.locator("#lib-grid .lcard.dim").count() == page.locator("#lib-grid .lcard").count() - 1
        page.screenshot(path=str(shots_dir / "stage-6-library.png"))
        page.fill("#lib-search", "")
        page.wait_for_function("document.querySelectorAll('#lib-grid .lcard.dim').length === 0")
        assert errors == []
    finally:
        ctx.close()
