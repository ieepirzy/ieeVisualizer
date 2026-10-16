"""Third round of review fixes: numbers beyond float64 in views, PNG area,
KPI numbers never split across lines, nothing non-zero shown as zero,
half-up rounding, float noise, shared references in copies."""

import collections
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from cost import measure

from ieeVisualizer.render import RenderError
from ieeVisualizer.spec import SpecParseError, SpecValidationError
from ieeVisualizer.spec._common import copy_tree
from ieeVisualizer.view import (
    Comparison,
    FigureComponent,
    Kpi,
    Stack,
    Text,
    ViewSpec,
    apply_view_patch,
    render_view,
    validate_view,
    view_from_json,
    view_to_json,
)
from ieeVisualizer.view import limits as L
from ieeVisualizer.view.draw import Label
from ieeVisualizer.view.examples import weekly_briefing
from ieeVisualizer.view.layout import Ctx, _kpi, _kpi_variants, kpi_lines, layout_view
from ieeVisualizer.view.text import format_number, text_width, wrap_text

ROOT = Path(__file__).resolve().parents[2]


def run_py(code: str, timeout: float) -> str:
    """Run ``code`` in a fresh interpreter (this checkout first on the path)."""
    e = dict(os.environ, MPLBACKEND="Agg")
    # tests/ for cost.py: the slow-path budgets are in its reference seconds
    e["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests"), e.get("PYTHONPATH", "")])
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True,
                       text=True, timeout=timeout, env=e, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return r.stdout


def _errors(view):
    return [(i.path, i.code) for i in validate_view(view) if i.severity == "error"]


# --------------------------------------------------------------------------
# numbers beyond float64 (after merging main's number_out_of_range)
# --------------------------------------------------------------------------


def test_kpi_value_beyond_float64_is_a_structured_error():
    big = "1" + "0" * 400
    for doc, where in (({"type": "kpi", "label": "k", "value": "BIG"}, "/root/value"),
                       ({"type": "kpi", "label": "k", "value": 1,
                         "comparison": {"label": "p", "value": "BIG"}},
                        "/root/comparison/value")):
        text = json.dumps({"schema_version": "1.0", "root": doc}).replace('"BIG"', big)
        with pytest.raises(SpecParseError) as ei:  # was a raw OverflowError
            view_from_json(text)
        assert [(i.path, i.code) for i in ei.value.issues] == [(where, "number_out_of_range")]
    for build, where in ((lambda: Kpi("k", 10 ** 400), "/value"),
                         (lambda: Comparison(-10 ** 400, "p"), "/value"),
                         (lambda: FigureComponent(figure="f", aspect=10 ** 400), "/aspect")):
        with pytest.raises(SpecParseError) as ei:
            build()
        assert [(i.path, i.code) for i in ei.value.issues] == [(where, "number_out_of_range")]
    v = ViewSpec(root=Stack([Kpi("k", 1.0, comparison=Comparison(1.0, "p"))]))
    v.root.children[0].value = 10 ** 400
    v.root.children[0].comparison.value = -10 ** 400
    assert _errors(v) == [("/root/children/0/value", "number_out_of_range"),
                          ("/root/children/0/comparison/value", "number_out_of_range")]


def test_figure_column_beyond_float64_in_a_view_is_a_structured_error():
    d = json.loads(view_to_json(weekly_briefing()))
    d["figures"]["trend"]["datasets"]["observed"]["columns"]["quotes"]["values"][0] = 10 ** 400
    v = view_from_json(json.dumps(d))
    where = "/figures/trend/datasets/observed/columns/quotes/values/0"
    assert _errors(v) == [(where, "number_out_of_range")]
    for fmt in ("svg", "png"):
        with pytest.raises(SpecValidationError) as ei:  # was OverflowError while drawing
            render_view(v, fmt)
        assert ei.value.issues[0].code == "number_out_of_range"


# --------------------------------------------------------------------------
# PNG area
# --------------------------------------------------------------------------


def test_png_area_is_limited_before_rasterising():
    assert L.MAX_PNG_PIXELS >= 1920 * L.MAX_PNG_SIDE_PX  # default width/scale: side binds
    v = ViewSpec(title="t", root=Stack([Text("\n\n".join("x" for _ in range(60)))]))
    height = layout_view(v, 4000)[1]
    assert height * 4 < L.MAX_PNG_SIDE_PX  # within the side limit ...
    assert 16000 * height * 4 > L.MAX_PNG_PIXELS  # ... but not the area limit
    with measure() as cost, pytest.raises(RenderError) as ei:
        render_view(v, "png", width=4000, scale=4)  # was rasterised: 16000 x 32000 px, 2 GB
    assert cost.seconds < 2, cost
    issue = ei.value.issues[0]
    assert issue.code == "view_too_large" and "megapixels" in issue.message
    render_view(v, "png", width=4000, scale=1)  # 4000 x height: allowed


# --------------------------------------------------------------------------
# KPI numbers: never split across lines
# --------------------------------------------------------------------------

_NUM = re.compile(r"\d[\d ]*(?:\.\d+)?(?:e[+−]\d+)?")

KPIS = [
    Kpi("Revenue", 123456789012.5, unit="EUR", decimals=2,
        comparison=Comparison(-98765432109.25, "last year")),
    Kpi("Revenue", 1e14 + 0.5, unit="EUR per booking, all channels", decimals=1),
    Kpi("Share", 99.97, unit="%", decimals=1, comparison=Comparison(12345678.9, "prev")),
    Kpi("Big", -1.7976931348623157e308, unit="EUR",
        comparison=Comparison(1.7976931348623157e308, "a very long comparison label")),
    Kpi("x" * 300, 123456789.125, unit="u" * 300, decimals=10, source="s" * 300,
        comparison=Comparison(-0.04, "y" * 300)),
]


@pytest.mark.parametrize("kpi", KPIS)
@pytest.mark.parametrize("width", [80.0, 120.0, 160.0, 300.0])
def test_kpi_numbers_appear_whole_on_one_line(kpi, width):
    """Every number drawn in a tile is one of the strings the formatter
    produced for it (as written or compact), never a piece of one: before,
    "123 456 789 012.50" wrapped at its thousands separators into lines that
    read as separate numbers."""
    whole = set()
    for _, nums in _kpi_variants(kpi):
        for s in nums:
            whole.update(_NUM.findall(s))
    items, _ = _kpi(kpi, "/root", 0.0, 0.0, width, Ctx(ViewSpec()))
    labels = [it for it in items if isinstance(it, Label)]
    for lb in labels:
        for m in _NUM.findall(lb.text):
            assert m in whole, (m, lb.text, width)
        assert lb.x + text_width(lb.text, lb.size, lb.bold, lb.italic) <= width - 16 + 1e-6, lb
    # and the value itself is on one line whenever it is a number at all
    value_lines = [lb for lb in labels if lb.bold]
    assert any(_NUM.search(lb.text) for lb in value_lines)


def test_wrapping_breaks_only_at_ascii_whitespace():
    for sep in (" ", " ", " "):
        w = text_width(f"aaaa{sep}bbbb", 12) + 1  # fits alone, not with " cccc"
        assert wrap_text(f"aaaa{sep}bbbb cccc", 12, w) == [f"aaaa{sep}bbbb", "cccc"]
    assert wrap_text("aa\tbb", 12, 18) == ["aa", "bb"]
    assert " " not in format_number(1234567) and " " in format_number(1234567)


# --------------------------------------------------------------------------
# nothing non-zero shown as zero; percent bound; half-up rounding; noise
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value, decimals, unit, expected", [
    (1.5e-5, 3, None, "<0.001"),
    (-1.5e-5, 3, None, ">−0.001"),
    (0.0, 3, None, "0.000"),
    (-0.0, 3, None, "0.000"),
    (0.3, 0, "EUR", "<1 EUR"),
    (5e-324, 10, None, "<0.0000000001"),
    (99.97, 1, "%", ">99.9%"),
    (100.03, 1, "%", "<100.1%"),
    (-99.97, 1, "%", "<−99.9%"),
    (100.0, 1, "%", "100.0%"),
    (99.97, 1, "pp", "100.0 pp"),  # only a percentage has 100 as a bound
])
def test_non_zero_kpi_is_never_shown_as_zero(value, decimals, unit, expected):
    assert kpi_lines(Kpi("k", value, unit=unit, decimals=decimals))[0] == expected


@pytest.mark.parametrize("value, decimals, expected", [
    (2.5, 0, "3"), (-2.5, 0, "−3"), (0.125, 2, "0.13"), (18.25, 1, "18.3"),
    (1.005, 2, "1.01"), (2.675, 2, "2.68"), (0.5, 0, "1"),
    (999_999_999_999_999.9, 0, "1e+15"),  # decided on the rounded value
    (999_999_999_999_999.4, 0, "999 999 999 999 999"),
    (999_999_999_999_999.9, None, "1e+15"),
    (0.1 + 0.2, None, "0.3"), (1 / 3, None, "0.333333333333333"), (0.1, None, "0.1"),
    (123456.789, None, "123 456.789"), (-1.5e-300, None, "−1.5e−300"),
])
def test_rounding_is_half_up_on_the_decimal_form(value, decimals, expected):
    assert format_number(value, decimals) == expected


def test_float32_keeps_its_own_shortest_form():
    np = pytest.importorskip("numpy")
    assert format_number(np.float32(0.1)) == "0.1"
    assert format_number(np.float64(0.1) + np.float64(0.2)) == "0.3"


# --------------------------------------------------------------------------
# copy_tree: memo, tuple subclasses
# --------------------------------------------------------------------------


def test_copy_tree_copies_shared_references_once():
    out = run_py("""
        from cost import measure
        from ieeVisualizer.spec._common import copy_tree
        a = [1]
        for _ in range(22):
            a = [a, a]
        with measure() as cost:
            c = copy_tree(a, limit=1000)
        print(cost.seconds, c[0] is c[1], c == a)
    """, timeout=60)  # was 22.9 s: every path copied again
    secs, shared, equal = out.split()
    assert float(secs) < 1 and shared == "True" and equal == "True"


def test_copy_tree_tuple_subclasses_and_limits():
    P = collections.namedtuple("P", "a b")
    out = copy_tree({"t": time.localtime(0), "p": P(1, [2]), "u": (1, (2,))})
    assert out["t"] == list(time.localtime(0)) and isinstance(out["t"], list)
    assert out["p"] == P(1, [2]) and type(out["p"]) is P
    assert out["u"] == (1, (2,)) and type(out["u"]) is tuple
    loop: list = []
    loop.append(loop)
    with pytest.raises(SpecParseError) as ei:
        copy_tree({"x": loop})
    assert ei.value.issues[0].code == "too_deep"
    deep = [0]
    for _ in range(200):
        deep = [deep]
    deeper = deep
    for _ in range(60):
        deeper = [deeper]
    with pytest.raises(SpecParseError) as ei:  # the shared part is reached deeper later
        copy_tree({"a": deep, "b": deeper})
    assert ei.value.issues[0].code == "too_deep"
    v = apply_view_patch(weekly_briefing(), [{"op": "add", "path": "/metadata/t",
                                              "value": time.localtime(0)}])
    assert v.metadata["t"] == list(time.localtime(0))


# --------------------------------------------------------------------------
# timings: an embedded figure's strings count twice
# --------------------------------------------------------------------------


def test_embedded_figure_strings_count_twice_in_the_text_budget():
    """matplotlib lays a figure's text out twice (auto legend, then draw):
    four figures with a 10 000-character title took 6.6 s as SVG at the
    default scale; now at most two pass."""
    import dataclasses

    fig = weekly_briefing().figures["trend"]

    def view(k):
        big = dataclasses.replace(fig, title="t" * 10_000)
        return ViewSpec(root=Stack([FigureComponent(spec=big) for _ in range(k)]))

    assert L.FIGURE_TEXT_WEIGHT == 2
    assert _errors(view(2)) == []
    assert _errors(view(3)) == [("/root/children/2", "too_much_text")]
