"""Phone and tablet legibility of real figure renders (docs/MOBILE_AND_EMAIL.md).

Measurements here are independent of the adapter's own audit: every
``Text.draw`` call made while the PNG is saved is recorded (pytest
monkeypatch), and each text drawn on the renderer that produced the PNG is
measured with matplotlib's own extents on that renderer, positions as saved.
Sizes are converted to the reading device: an image ``W`` px wide shown
``css`` CSS px wide on a screen with ``dpr`` device px per CSS px.
"""

from __future__ import annotations

import struct

import pytest
from matplotlib.text import Text

from ieeVisualizer import templates
from ieeVisualizer.network import NetworkSpec, render_network
from ieeVisualizer.render import (
    LegibilityError,
    RenderRequest,
    check_legible,
    get_profile,
    list_profiles,
    render,
)
from ieeVisualizer.render.mpl.legibility import recording_canvas

#: Device pixel ratio of the phone the measurements assume (a 3x phone).
PHONE_DPR = 3.0
PHONE = "email-phone"
TABLET = "email-tablet"


def png_size(content: bytes) -> tuple[int, int]:
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", content[16:24])


def measure(monkeypatch, spec, request, css=None):
    """Render ``spec`` and measure every text drawn into the PNG.

    Returns ``(result, texts)``; each text is a dict with its string, font
    size and line height in CSS px as shown at the profile's width, line
    height in device px on a ``PHONE_DPR`` screen, and its box in image px."""
    calls = []
    original = Text.draw

    def spy(self, renderer):
        calls.append((self, renderer))
        return original(self, renderer)

    saved = []
    from matplotlib.figure import Figure

    original_save = Figure.savefig

    def save_spy(self, *a, **kw):
        saved.append(self)
        return original_save(self, *a, **kw)

    monkeypatch.setattr(Text, "draw", spy)
    monkeypatch.setattr(Figure, "savefig", save_spy)
    if isinstance(spec, NetworkSpec):
        result = render_network(spec, request)
    else:
        result = render(spec, request)
    monkeypatch.undo()
    assert len(saved) == 1
    renderer = calls[-1][1]  # the save is the last draw: its renderer made the PNG
    width_px, height_px = png_size(result.content)
    css = css or request.display_width or get_profile(request.profile).css_width
    css_per_px = css / width_px
    texts, seen = [], set()
    for t, r in calls:
        if r is not renderer or id(t) in seen or not t.get_visible() or not t.get_text().strip():
            continue
        seen.add(id(t))
        box = t.get_window_extent(renderer)
        n = t.get_text().count("\n") + 1
        upright = abs(t.get_rotation() % 180 - 90) < 1
        line_px = (box.width if upright else box.height) / n
        texts.append({
            "text": t.get_text(), "box": box,
            "em_css": renderer.points_to_pixels(t.get_fontsize()) * css_per_px,
            "line_css": line_px * css_per_px,
            "line_device_px": line_px * css_per_px * PHONE_DPR,
        })
    return result, texts, (width_px, height_px)


def problems(texts, size, min_css):
    w, h = size
    small = [(round(t["em_css"], 1), t["text"]) for t in texts if t["em_css"] < min_css - 1e-6]
    clipped = [t["text"] for t in texts if t["box"].x0 < -1 or t["box"].y0 < -1
               or t["box"].x1 > w + 1 or t["box"].y1 > h + 1]
    overlaps = []
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            A, B = a["box"], b["box"]
            if min(A.x1, B.x1) - max(A.x0, B.x0) > 1 and min(A.y1, B.y1) - max(A.y0, B.y0) > 1:
                overlaps.append((a["text"], b["text"]))
    return {"small": small, "clipped": clipped, "overlaps": overlaps}


def example(name):
    t = templates.get(name)
    return t.build_payload(t.example())


# --------------------------------------------------------------------------
# realistic figures at phone and tablet width
# --------------------------------------------------------------------------

LEGIBLE = ["timeseries", "bar_comparison", "vector_field_2d"]


@pytest.mark.parametrize("profile", [PHONE, TABLET])
@pytest.mark.parametrize("name", LEGIBLE)
def test_template_examples_are_legible(monkeypatch, name, profile):
    prof = get_profile(profile)
    request = prof.figure_request("png")
    result, texts, size = measure(monkeypatch, example(name), request)
    assert size[0] == prof.pixel_width  # the image is as wide as the profile says
    assert len(texts) >= 8  # title, axis labels, ticks: the measurement saw the figure
    assert problems(texts, size, prof.min_text_px) == {"small": [], "clipped": [],
                                                       "overlaps": []}
    # rendered line boxes on a 3x phone: matplotlib reserves about one em
    # (ascender to descender of "lp": 0.96 em under matplotlib 3.9, 1.0 under
    # 3.10+), so every line is at least 0.9 x the 12 px minimum x 3 device px
    assert min(t["line_device_px"] for t in texts) >= 0.9 * prof.min_text_px * PHONE_DPR
    assert not [w for w in result.warnings if w.code.startswith("legibility_")]
    check_legible(result)


def test_without_a_profile_the_same_image_is_too_small_on_a_phone(monkeypatch):
    """What main can express: the same 1200 px image at the same density, no
    profile. matplotlib's 10 pt tick labels come out at about 10 CSS px on a
    320 px phone column, and nothing says so."""
    spec = example("timeseries")
    plain = RenderRequest(format="png", width=1200, height=960, units="px", dpi=270.0)
    result, texts, size = measure(monkeypatch, spec, plain, css=320)
    assert size == (1200, 960)
    small = problems(texts, size, 12.0)["small"]
    assert small and min(px for px, _ in small) < 10.5
    assert not [w for w in result.warnings if w.code.startswith("legibility_")]


def test_funnel_on_a_phone_is_reported_not_hidden(monkeypatch):
    """The funnel's rate column collides with the first value label at phone
    width; the audit says so and the independent measurement agrees."""
    request = get_profile(PHONE).figure_request("png")
    result, texts, size = measure(monkeypatch, example("funnel"), request)
    found = problems(texts, size, 12.0)
    assert found["small"] == [] and found["clipped"] == []
    assert found["overlaps"]
    codes = {w.code for w in result.warnings}
    assert "legibility_text_overlap" in codes
    with pytest.raises(LegibilityError) as exc:
        check_legible(result)
    assert exc.value.issues[0].code == "legibility_text_overlap"


def test_network_text_below_minimum_is_reported(monkeypatch):
    """Network renders get the audit but not yet the adaptations (follow-up):
    their 7.5 pt labels are reported, and measured, as too small."""
    request = get_profile(PHONE).figure_request("png")
    result, texts, size = measure(monkeypatch, example("org_chart"), request)
    assert problems(texts, size, 12.0)["small"]
    assert "legibility_text_too_small" in {w.code for w in result.warnings}


def test_too_many_categories_say_so_and_keep_every_label(monkeypatch):
    from ieeVisualizer.spec import Axis, BarMark, DataSnapshot, FigureSpec

    names = [f"Region {i:02d}" for i in range(40)]
    spec = FigureSpec(
        title="Sales by region",
        datasets={"d": DataSnapshot.from_columns({"r": names, "v": list(range(40))})},
        layers=[BarMark(data="d", encoding={"x": "r", "y": "v"})],
        axes={"x": Axis(label="Region"), "y": Axis(label="Sales")})
    result, texts, _ = measure(monkeypatch, spec, get_profile(PHONE).figure_request("png"))
    assert {n for n in names} <= {t["text"] for t in texts}  # nothing dropped
    codes = {w.code for w in result.warnings}
    assert "too_many_categories_for_profile" in codes
    assert "legibility_text_overlap" in codes


def test_category_labels_turn_upright_before_giving_up(monkeypatch):
    from ieeVisualizer.spec import Axis, BarMark, DataSnapshot, FigureSpec

    names = ["Northern", "Southern", "Eastern", "Western", "Central", "Coastal"]
    spec = FigureSpec(
        datasets={"d": DataSnapshot.from_columns({"r": names, "v": [3, 5, 2, 6, 4, 1]})},
        layers=[BarMark(data="d", encoding={"x": "r", "y": "v"})],
        axes={"x": Axis(label="Region")})
    result, texts, size = measure(monkeypatch, spec, get_profile(PHONE).figure_request("png"))
    assert problems(texts, size, 12.0) == {"small": [], "clipped": [], "overlaps": []}
    upright = [t for t in texts if t["text"] in names]
    assert len(upright) == 6 and all(t["box"].height > t["box"].width for t in upright)


# --------------------------------------------------------------------------
# the profile contract
# --------------------------------------------------------------------------


def test_profiles_map_points_to_css_px():
    for p in list_profiles():
        req = p.figure_request("png")
        assert req.size_px[0] == p.pixel_width
        assert req.dpi == pytest.approx(72 * p.scale)
        # at dpi = 72 * scale one point is one CSS px, so the minimum in pt is the px value
        assert p.min_text_pt(req.size_in[0]) == pytest.approx(p.min_text_px)
    assert get_profile(PHONE).narrow and not get_profile(TABLET).narrow


def test_request_profile_validation_and_record():
    with pytest.raises(ValueError, match="unknown render profile"):
        RenderRequest(profile="watch")
    with pytest.raises(ValueError, match="needs a profile"):
        RenderRequest(display_width=300)
    req = get_profile(PHONE).figure_request("png", display_width=280)
    assert req.to_dict()["profile"] == PHONE and req.to_dict()["display_width"] == 280
    assert "profile" not in RenderRequest().to_dict()  # old requests record what they did


def test_line_boxes_match_matplotlib_extents():
    """The audit's per-line boxes (the renderer's metrics of the string drawn)
    agree with matplotlib's own window extent along the line and lie inside
    it across (the extent reserves a full line height, ascender to
    descender, whatever the characters), upright or turned."""
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 3), dpi=200)
    texts = [fig.text(0.1, 0.5, "Sales (EUR)", fontsize=12),
             fig.text(0.6, 0.2, "Region 07", fontsize=12, rotation=90)]
    canvas = recording_canvas(fig)
    canvas.draw()
    lines = canvas.renderer.lines
    assert len(lines) == 2
    for line, t in zip(lines, texts):
        box = t.get_window_extent(canvas.renderer)
        along = (line.x0, line.x1) if t.get_rotation() == 0 else (line.y0, line.y1)
        extent = (box.x0, box.x1) if t.get_rotation() == 0 else (box.y0, box.y1)
        assert along == pytest.approx(extent, abs=2)
        assert box.x0 - 3 <= line.x0 <= line.x1 <= box.x1 + 3  # glyph overhang (mpl 3.9)
        assert box.y0 - 3 <= line.y0 <= line.y1 <= box.y1 + 3


def test_no_profile_renders_keep_their_bytes():
    spec = example("timeseries")
    a = render(spec, RenderRequest(format="svg"))
    b = render(spec, RenderRequest(format="svg"))
    assert a.content == b.content
    assert not [w for w in a.warnings if w.code.startswith("legibility_")]
