"""Missing glyphs are found from the fonts themselves, per text artist: the
answer cannot depend on the host's logging or warnings configuration, or on
other renders running at the same time."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import warnings
from collections import Counter
from pathlib import Path

import pytest

from ieeVisualizer.render import RenderRequest, get_profile, render
from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = {
    "matplotlib logger at ERROR": 'logging.getLogger("matplotlib").setLevel(logging.ERROR)',
    "logging.disable": "logging.disable(logging.WARNING)",
    "basicConfig ERROR": "logging.basicConfig(level=logging.ERROR)",
    "propagate off": 'logging.getLogger("matplotlib").propagate = False',
}


def fig(title: str) -> FigureSpec:
    data = DataSnapshot.from_columns({"x": [0, 1, 2], "y": [1, 2, 3]})
    return FigureSpec(title=title, datasets={"d": data},
                      layers=[LineMark(data="d", encoding={"x": "x", "y": "y"})])


def missing(title: str, request=None) -> bool:
    result = render(fig(title), request or get_profile("email-phone").figure_request("png"))
    return "glyph_missing" in {w.code for w in result.warnings}


@pytest.mark.parametrize("config", sorted(CONFIGS))
def test_mathtext_without_glyphs_is_found_whatever_logging_says(config):
    code = textwrap.dedent(f"""
        import logging, sys, warnings
        warnings.simplefilter("ignore")
        {CONFIGS[config]}
        sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / "tests" / "render")!r}]
        from test_glyph_check import missing
        print([missing(t) for t in (r"$x_{{東}}$", r"$\\mathcal{{東}}$", r"$x^2$ äöå")])
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, check=True).stdout.strip().splitlines()[-1]
    assert out == "[True, True, False]"


def test_host_warning_filters_do_not_change_the_answer():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert missing("東京 売上") and not missing("Hyvä äöå")
    with warnings.catch_warnings():
        warnings.filterwarnings("error", module="matplotlib")
        assert missing("東京 売上", RenderRequest()) and not missing("ok", RenderRequest())
        with pytest.raises(UserWarning):  # the host's filter is still in force afterwards
            warnings.warn_explicit("x", UserWarning, "f.py", 1, module="matplotlib.text")


@pytest.mark.filterwarnings("ignore:Glyph .* missing from font")
def test_concurrent_renders_neither_miss_nor_invent_missing_glyphs():
    """The reviewer's case: four threads, two CJK and two ASCII titles, 15
    renders each."""
    seen: Counter = Counter()
    lock = threading.Lock()

    def work(title: str) -> None:
        for _ in range(15):
            got = missing(title)
            with lock:
                seen[(title, got)] += 1

    threads = [threading.Thread(target=work, args=(t,))
               for t in ("東京 売上", "Plain ascii", "東京 売上", "Plain ascii")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == Counter({("東京 売上", True): 30, ("Plain ascii", False): 30})


def test_controls_and_stray_carriage_returns_are_looked_up_not_skipped():
    """A CSV row ending "\\r" draws a box after the category name."""
    from ieeVisualizer.spec import BarMark

    data = DataSnapshot.from_columns({"city": ["Helsinki\r", "Espoo"], "n": [3, 4]})
    spec = FigureSpec(datasets={"d": data},
                      layers=[BarMark(data="d", encoding={"x": "city", "y": "n"})])
    result = render(spec, RenderRequest())
    assert "glyph_missing" in {w.code for w in result.warnings}
    assert missing("Sales\r") and missing("a\tb") and not missing("a b")


def test_a_literal_currency_sign_does_not_hide_mathtext_dummies():
    assert missing("Hinta ¤ $x$ 東京") and not missing("Hinta ¤ $x$")


def test_default_ignorables_follow_the_text_engine():
    """matplotlib >= 3.11 shapes text: a variation selector the font lacks
    draws nothing; before that it is drawn as a box."""
    import matplotlib
    from matplotlib.font_manager import FontProperties

    from ieeVisualizer.render.mpl.legibility import missing_glyphs_in

    shaped = tuple(int(x) for x in matplotlib.__version__.split(".")[:2]) >= (3, 11)
    mono = FontProperties(family="DejaVu Sans Mono")  # an inline `code` run
    check, vs16 = chr(0x2714), chr(0xFE0F)
    assert missing_glyphs_in(check, mono) == ""
    assert missing_glyphs_in(check + vs16, mono) == ("" if shaped else vs16)


def test_the_check_adds_nothing_to_the_hosts_log():
    import logging

    from matplotlib.font_manager import FontProperties

    from ieeVisualizer.render.mpl.legibility import missing_glyphs_in

    records: list = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    root, keep = logging.getLogger(), Keep(logging.DEBUG)
    root.addHandler(keep)
    mathtext = logging.getLogger("matplotlib.mathtext")
    before = list(mathtext.filters)
    try:
        assert missing_glyphs_in("$x_{東}$", FontProperties(), math=True)
    finally:
        root.removeHandler(keep)
    assert [r.getMessage() for r in records if "glyph" in r.getMessage()] == []
    assert mathtext.filters == before
