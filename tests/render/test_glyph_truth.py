"""The glyph checker against pixel ground truth on the running matplotlib.

Each character is drawn alone with Agg and compared with how this matplotlib
draws a glyph it does not have: matplotlib >= 3.11 draws the LastResort font's
box, earlier versions the font's .notdef box (as an unassigned code point,
U+0378, draws). A character that draws nothing is not missing. Mathtext is
drawn whole and again with one character replaced by an unassigned one: if
the picture does not change, that character was drawn as mathtext's dummy.
``missing_glyphs_in`` must agree with every verdict (CI runs matplotlib 3.9
and 3.11, which differ for tabs, wide spaces and default-ignorables).
"""

from __future__ import annotations

import io
import logging
import os
import warnings

import matplotlib
import numpy as np
import pytest
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, findfont

from ieeVisualizer.render.mpl.legibility import missing_glyphs_in

V = tuple(int(x) for x in matplotlib.__version__.split(".")[:2])
LAST_RESORT = os.path.join(matplotlib.get_data_path(), "fonts", "ttf",
                           "LastResortHE-Regular.ttf")
UNASSIGNED = "͸"
CHARS = [chr(c) for c in (
    0x61, 0xE4, 0xC5, 0x20AC, 0x2713, 0x26A0,          # a ä Å € ✓ ⚠
    0x0D, 0x09, 0x1C, 0x85,                            # \r \t and other controls
    0x3000, 0xA0, 0x2003, 0x2007, 0x205F, 0x1680,      # spaces (U+1680 has ink)
    0x200D, 0xFE0F, 0x034F, 0x200B,                    # ZWJ, VS16, CGJ, ZWSP
    0x0301,                                            # combining acute
    0x3164, 0x115F, 0xFFA0, 0x1BCA0, 0x180E, 0x180F,   # ignorables HarfBuzz still boxes
    0x6771, 0x1F600, 0xA4)]                            # 東 😀 ¤


def _font_configs() -> dict:
    configs = {
        "sans": FontProperties(family="DejaVu Sans"),
        "sans-bold": FontProperties(family="DejaVu Sans", weight="bold"),
        "sans-italic": FontProperties(family="DejaVu Sans", style="italic"),
        "mono": FontProperties(family="DejaVu Sans Mono"),
        "unknown-then-dejavu": FontProperties(family=["NoSuchFont", "DejaVu Sans"]),
    }
    if "Liberation" in findfont(FontProperties(family="Liberation Sans")):
        configs["liberation-then-dejavu"] = FontProperties(family=["Liberation Sans",
                                                                   "DejaVu Sans"])
    from ieeVisualizer.render.mpl.style import with_fallbacks

    if len(with_fallbacks("DejaVu Sans")) > 1:  # the installed CJK fallback (test_font_fallback)
        configs["dejavu-then-cjk"] = FontProperties(family=with_fallbacks("DejaVu Sans"))
    return configs


def _image(text: str, prop: FontProperties, math: bool = False) -> np.ndarray:
    fig = Figure(figsize=(2, 0.8), dpi=40)
    fig.text(0.05, 0.3, text, fontproperties=prop, fontsize=20, parse_math=math)
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        logging.disable(logging.CRITICAL)
        try:
            fig.savefig(buf, format="raw")
        finally:
            logging.disable(logging.NOTSET)
    return np.frombuffer(buf.getvalue(), dtype=np.uint8)


def _drawn_as_missing(ch: str, prop: FontProperties) -> bool:
    picture = _image(ch, prop)
    if (picture == 255).all():  # draws nothing at all
        return False
    if V >= (3, 11):
        box = _image(ch, FontProperties(fname=LAST_RESORT, size=prop.get_size()))
        return bool((picture == box).all())
    return bool((picture == _image(UNASSIGNED, prop)).all())


FONTS = _font_configs()


@pytest.mark.parametrize("name", sorted(FONTS))
def test_plain_text_agrees_with_the_pixels(name):
    prop = FONTS[name]
    wrong = [(f"U+{ord(ch):04X}", truth, bool(missing_glyphs_in(ch, prop)))
             for ch in CHARS
             for truth in [_drawn_as_missing(ch, prop)]
             if truth != bool(missing_glyphs_in(ch, prop))]
    assert wrong == [], f"matplotlib {matplotlib.__version__}: (char, drawn missing, checker)"


MATH = ["$x_{東}$", r"$\mathcal{東}$", r"$\mathrm{ä}$", "Hinta ¤ $x$ 東京", "$x$ ¤", "$x^2$ ok"]


@pytest.mark.parametrize("fontset", ["dejavusans", "stix", "cm"])
def test_mathtext_agrees_with_the_pixels(fontset):
    wrong = []
    with matplotlib.rc_context({"mathtext.fontset": fontset}):
        prop = FontProperties()
        for s in MATH:
            whole = _image(s, prop, True)
            # "¤" is itself the dummy glyph, so replacing it proves nothing
            truth = any((_image(s.replace(c, UNASSIGNED), prop, True) == whole).all()
                        for c in dict.fromkeys(s) if ord(c) > 127 and c != "¤")
            if truth != bool(missing_glyphs_in(s, prop, math=True)):
                wrong.append((s, truth))
    assert wrong == [], f"matplotlib {matplotlib.__version__}"
