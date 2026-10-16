"""An installed CJK font is a fallback after the theme's families, in
figures and in view PNG labels; characters no installed font has are still
``glyph_missing`` (docs/MOBILE_AND_EMAIL.md, "Font fallback")."""

from __future__ import annotations

import io
import os

import pytest

from ieeVisualizer.render import check_legible, get_profile, render
from ieeVisualizer.render.mpl.style import fallback_families, with_fallbacks
from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark

#: Old Hungarian: no font this project knows of covers it.
NO_FONT = "\U00010c80\U00010c81"


@pytest.fixture
def cjk():
    if not fallback_families():
        if os.environ.get("IEEV_REQUIRE_CJK_FONT", "").lower() in ("1", "true"):
            pytest.fail("no CJK font installed; CI installs fonts-wqy-zenhei")
        pytest.skip("no CJK font installed (e.g. fonts-noto-cjk or fonts-wqy-zenhei)")


def fig(title: str) -> FigureSpec:
    data = DataSnapshot.from_columns({"x": [0, 1, 2], "y": [1, 2, 3]})
    return FigureSpec(title=title, datasets={"d": data},
                      layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})])


def _pixels(title: str):
    from matplotlib.image import imread

    result = render(fig(title), get_profile("email-phone").figure_request("png"))
    return result, imread(io.BytesIO(result.content), format="png")


def test_cjk_text_is_drawn_with_the_installed_fallback(cjk):
    a, pa = _pixels("売上 東")
    b, pb = _pixels("売上 京")
    check_legible(a)  # no glyph_missing
    check_legible(b)
    # empty boxes would be the same picture; two real glyphs are not
    assert pa.shape == pb.shape and (pa != pb).any()


def test_a_character_no_font_has_is_still_refused(cjk):
    result = render(fig(f"Sales {NO_FONT}"), get_profile("email-phone").figure_request("png"))
    assert "glyph_missing" in {w.code for w in result.warnings}


def test_view_labels_use_the_fallback_too(cjk):
    from ieeVisualizer.view.draw import Label, label_font

    assert label_font(Label(0, 0, "x", 13)).get_family()[-1] == fallback_families()[0]
    assert with_fallbacks("DejaVu Sans")[0] == "DejaVu Sans"  # the theme's font comes first
