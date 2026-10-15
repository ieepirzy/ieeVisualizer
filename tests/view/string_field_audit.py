"""Run by test_view_hardening_2.py in a subprocess (not collected by pytest).

For every string field of a view that reaches layout or rendering, builds
the view holding a 10 000-character value (one unbroken word, and words) as
many times as validation allows, renders SVG, HTML and PNG (at the default
scale, 2) and
prints name, shape, count, seconds per format tab-separated. A PNG
refused with view_too_large prints refused <seconds>.
"""
import dataclasses
import sys
import time

from ieeVisualizer.render import RenderError
from ieeVisualizer.spec import DataSnapshot, Provenance
from ieeVisualizer.view import (
    Card,
    Comparison,
    FigureComponent,
    Flow,
    FlowEdge,
    FlowNode,
    Kpi,
    Note,
    Stack,
    Table,
    Text,
    ViewSpec,
    render_view,
    validate_view,
)
from ieeVisualizer.view.examples import weekly_briefing

FIG = weekly_briefing().figures["trend"]

def flow(k, s, where):
    n = max(k, 2)
    nodes = [FlowNode(s[:-4] + f"{i:04d}" if where == "id" else f"n{i}",
                      s if where == "label" and i < k else f"N{i}",
                      group=s if where == "group" and i < k else None) for i in range(n)]
    ids = [x.id for x in nodes]
    edges = [FlowEdge(ids[i], ids[(i + 1) % n], label=s if where in ("edge", "causal") else "x",
                      kind="causal" if where == "causal" else "dataflow")
             for i in range(k if where in ("edge", "causal") else n - 1)]
    return Flow(nodes=nodes, edges=edges, title=s if where == "title" else None,
                description=s if where == "description" else None)

def table(k, s, where):
    if where == "cell":
        ds = DataSnapshot.from_columns({"c": [s] * k})
    elif where == "name":
        ds = DataSnapshot.from_columns({s[:-3] + f"{i:03d}": [1] for i in range(k)})
    elif where == "unit":
        ds = DataSnapshot.from_columns({f"c{i}": [1] for i in range(k)},
                                       units={f"c{i}": s for i in range(k)})
    else:
        ds = DataSnapshot.from_columns({"c": [1]})
    return ds, Table(data="d", title=s if where == "title" else None,
                     max_rows=0 if where in ("name", "unit") else None)

def single(build):
    return lambda k, s: ViewSpec(root=Stack([build(s) for _ in range(k)]))

FIELDS = {
    "view.title": lambda k, s: ViewSpec(title=s, root=Stack([Text("x")])) if k == 1 else None,
    "card.title": single(lambda s: Card([Text("x")], title=s)),
    **{f"text.{st}": single(lambda s, st=st: Text(s, style=st))
       for st in ("body", "lead", "heading", "small")},
    "caveat": single(lambda s: Note(s)),
    "footnote": single(lambda s: Note(s, type="footnote")),
    "kpi.label": single(lambda s: Kpi(s, 1.5)),
    "kpi.unit": single(lambda s: Kpi("k", 1e308, unit=s, comparison=Comparison(-1e308, "p"))),
    "kpi.source": single(lambda s: Kpi("k", 1.5, source=s)),
    "kpi.comparison.label": single(lambda s: Kpi("k", 1.5, comparison=Comparison(1.0, s))),
    **{f"table.{w}": (lambda k, s, w=w: (lambda ds, t: ViewSpec(
        root=Stack([t] * (1 if w in ("cell", "name", "unit") else k)), datasets={"d": ds}))(
        *table(k, s, w))) for w in ("title", "name", "unit", "cell")},
    **{f"flow.{w}": (lambda k, s, w=w: ViewSpec(root=Stack(
        [flow(k, s, w)] if w in ("label", "group", "id", "edge", "causal")
        else [flow(1, s, w) for _ in range(k)]))) for w in
       ("title", "description", "label", "group", "id", "edge", "causal")},
    "figure.title": single(lambda s: FigureComponent(spec=dataclasses.replace(FIG, title=s))),
    "figure.caption": single(lambda s: FigureComponent(spec=dataclasses.replace(FIG, caption=s))),
    "metadata": lambda k, s: ViewSpec(root=Stack([Text("x")]),
                                      metadata={f"k{i}": s for i in range(k)}),
    "provenance.notes": lambda k, s: ViewSpec(root=Stack([Table(data=f"d{i}") for i in range(k)]),
        datasets={f"d{i}": DataSnapshot.from_columns({"c": [1]}, provenance=Provenance(notes=s))
                  for i in range(k)}),
}

def ok(v):
    return v is not None and not [i for i in validate_view(v) if i.severity == "error"]

def max_count(build, s, cap=1024):
    if not ok(build(1, s)):
        return 0
    lo, hi = 1, 2
    while hi <= cap and ok(build(hi, s)):
        lo, hi = hi, hi * 2
    hi = min(hi, cap + 1)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(build(mid, s)) else (lo, mid)
    return lo

# argv: field names to run, or "i/n" to run every n-th field from the i-th
# (the test runs parts in parallel). PNG is drawn for the unbroken-word shape
# only: matplotlib's cost per glyph is the same for both shapes.
args = sys.argv[1:]
names = list(FIELDS)
if len(args) == 1 and "/" in args[0]:
    i, n = map(int, args[0].split("/"))
    names = names[i::n]
elif args:
    names = args
print("FIELDS", len(FIELDS), len(names), sep="\t", flush=True)
for name in names:
    build = FIELDS[name]
    for shape, s in (("word", "x" * 10_000), ("words", ("word " * 2000)[:10_000])):
        k = max_count(build, s)
        v = build(k, s)
        times = []
        for fmt in ("svg", "html", "png") if shape == "word" else ("svg", "html"):
            t = time.time()
            try:
                render_view(v, fmt)
                times.append(str(round(time.time() - t, 2)))
            except RenderError as e:
                assert e.issues[0].code == "view_too_large", e
                times.append(f"refused {round(time.time() - t, 2)}")
        print(name, shape, k, *times, sep="\t", flush=True)
