"""The email HTML laid out by a real browser engine (headless Chromium via
Playwright) at phone and tablet widths: no horizontal overflow, no text over
text, no text below the HTML minimum, KPI tiles stacked on phones and side by
side on tablets, images inside the screen.

Skipped with the reason when Playwright or Chromium is missing; CI sets
``IEEV_REQUIRE_BROWSER=1`` (and installs both), where a missing browser fails.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from ieeVisualizer.spec import DataSnapshot
from ieeVisualizer.view import Card, Grid, Kpi, Note, Stack, Table, Text, ViewSpec, render_email
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing

WIDTHS = (320, 360, 430, 768)
URL = "https://reports.example.org/" + "a1b2c3d4e5" * 12  # 140 characters, no break point

MEASURE = """() => {
  const out = {sw: document.documentElement.scrollWidth, iw: window.innerWidth,
               minFont: 1e9, boxes: [], tiles: [], imgs: []};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, k = 0;
  while ((n = walker.nextNode())) {
    if (!n.textContent.trim()) continue;
    k += 1;
    out.minFont = Math.min(out.minFont, parseFloat(getComputedStyle(n.parentElement).fontSize));
    const r = document.createRange(); r.selectNodeContents(n);
    for (const b of r.getClientRects())
      if (b.width > 0 && b.height > 0)
        out.boxes.push([k, b.left, b.top, b.right, b.bottom, n.textContent.trim().slice(0, 30)]);
  }
  for (const d of document.querySelectorAll('div[style*="inline-block"]')) {
    if (!d.textContent.includes("KPI")) continue;
    const b = d.getBoundingClientRect(); out.tiles.push([b.left, b.top]);
  }
  for (const i of document.images) {
    const b = i.getBoundingClientRect(); out.imgs.push([b.left, b.right]);
  }
  return out;
}"""


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as first:  # noqa: BLE001 - try a browser that is installed anyway
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
        for exe in sorted(glob.glob(f"{root}/chromium-*/chrome-linux*/chrome")):
            return p.chromium.launch(executable_path=exe)
        raise first


@pytest.fixture(scope="module")
def browser():
    why = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        why = "Playwright is not installed (pip install playwright)"
    if why is None:
        manager = sync_playwright().start()
        try:
            b = _launch(manager)
        except Exception as exc:  # noqa: BLE001
            manager.stop()
            why = f"Chromium cannot be launched ({str(exc).splitlines()[0]})"
    if why is not None:
        if os.environ.get("IEEV_REQUIRE_BROWSER"):
            pytest.fail(f"browser layout tests are required here but {why}")
        pytest.skip(why)
    yield b
    b.close()
    manager.stop()


def hostile() -> ViewSpec:
    """Every text container holding a long unbreakable string, plus tiles."""
    ds = DataSnapshot.from_columns({"link": [URL, "short"], "n": [1, 2]})
    return ViewSpec(
        title=f"Report {URL}", datasets={"t": ds},
        root=Stack(children=[
            Text(text=f"See {URL} for details."),
            Grid(columns=3, children=[Kpi(label=f"KPI {i} {URL}", value=1234.5 * i, unit="EUR")
                                      for i in range(1, 4)]),
            Card(title=f"Card {URL}", children=[Text(text="inside")]),
            Note(type="caveat", text=f"Source {URL}"),
            Table(data="t", title=f"Table {URL}")]))


def kpis() -> ViewSpec:
    return ViewSpec(root=Grid(columns=3, children=[
        Kpi(label=f"KPI {i}", value=1234567.0 * i, unit="EUR", source="ledger")
        for i in range(1, 4)]))


def layout(browser, tmp_path: Path, view: ViewSpec, width: int) -> dict:
    email = render_email(view, image_src=lambda name: name)
    for name, content in email.images.items():
        (tmp_path / name).write_bytes(content)
    page = tmp_path / "email.html"
    page.write_text(email.html, encoding="utf-8")
    p = browser.new_page(viewport={"width": width, "height": 900})
    try:
        p.goto(page.as_uri())
        return p.evaluate(MEASURE)
    finally:
        p.close()


def overlaps(boxes) -> list:
    out = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] != b[0] and min(a[3], b[3]) - max(a[1], b[1]) > 1 \
                    and min(a[4], b[4]) - max(a[2], b[2]) > 1:
                out.append((a[5], b[5]))
    return out


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("build", [weekly_briefing, reporting_pipeline, hostile, kpis])
def test_email_lays_out_inside_the_screen(browser, tmp_path, build, width):
    m = layout(browser, tmp_path, build(), width)
    assert m["sw"] <= m["iw"], f"{m['sw']} px wide on a {m['iw']} px screen"
    assert m["boxes"], "no text was laid out"
    assert overlaps(m["boxes"]) == []
    assert m["minFont"] >= 13
    assert all(left >= 0 and right <= m["iw"] + 0.5 for left, right in m["imgs"])


@pytest.mark.parametrize("width", WIDTHS)
def test_email_kpi_tiles_stack_on_phones_and_pair_on_tablets(browser, tmp_path, width):
    tiles = layout(browser, tmp_path, kpis(), width)["tiles"]
    assert len(tiles) == 3
    rows = len({round(top) for _, top in tiles})
    assert rows == (3 if width <= 430 else 2)
