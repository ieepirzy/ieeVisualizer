"""Gallery: every built-in template example, every mark type and the legacy
bridge, rendered and exported with provenance manifests; the business
templates also under the Muutto365 themes.

Run: python examples/gallery.py  (writes to examples/out/, which is gitignored)

For each entry ``<name>`` it writes

    <name>.png, <name>.svg       the rendered figure
    <name>.spec.json             the canonical FigureSpec that was rendered
    <name>.manifest.json         the VisualizationArtifact manifest

through :func:`ieeVisualizer.artifacts.export` with :class:`FlatWriter`, a
writer that stores one flat file per artifact file instead of the default
``<root>/<artifact_id>/...`` layout, so the gallery is easy to browse. The
manifest still lists the files under their artifact names (``figure.png``,
``spec.json``); :func:`output_path` maps one to the other.

Two composed views (``ieeVisualizer.view``) are exported the same way by
:func:`export_views`: ``view-reporting-pipeline`` (the section 5.2 flow
diagram) and ``view-weekly-briefing`` (KPI tiles, a timeseries figure, a
table and caveats), each as ``.html``, ``.svg`` and ``.png`` plus
``.view.json``, ``.figure-<n>.spec.json`` and ``.manifest.json``.

The mark examples use small synthetic data (formulas or a seeded generator),
labelled as such in their provenance; none of it is a measurement.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import numpy as np
import sympy as sp

from ieeVisualizer import Visualizer, templates
from ieeVisualizer.artifacts import export
from ieeVisualizer.network import Edge, Layout, NetworkSpec, Node, Quantity, export_network
from ieeVisualizer.render import RenderRequest
from ieeVisualizer.spec import (
    AreaMark,
    Axis,
    BarMark,
    DataSnapshot,
    Encoding,
    FigureSpec,
    FunctionMark,
    HeatmapMark,
    HistogramMark,
    LineMark,
    Provenance,
    QuiverMark,
    ReferenceLine,
    ScatterMark,
)
from ieeVisualizer.view import export_view
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing

OUT = Path(__file__).parent / "out"
FORMATS = ("png", "svg")
REQUEST = RenderRequest(width=6.4, height=4.4, dpi=100)

SYNTHETIC = Provenance(source="examples/gallery.py",
                       notes="Synthetic values for the gallery (formula or seeded generator); "
                             "not a measurement.")


def output_path(out: Path, name: str, file: str) -> Path:
    """Where :class:`FlatWriter` puts artifact file ``file`` of entry ``name``:
    ``figure.<ext>`` -> ``<name>.<ext>``, anything else -> ``<name>.<file>``."""
    stem, ext = os.path.splitext(file)
    return Path(out) / (f"{name}{ext}" if stem == "figure" else f"{name}.{file}")


class FlatWriter:
    """ArtifactWriter storing ``<out>/<name>.<ext>`` files for one gallery entry."""

    def __init__(self, out: Path, name: str) -> None:
        self.out, self.name = Path(out), name

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        path = output_path(self.out, self.name, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)


def _snap(columns, units=None, dtypes=None) -> DataSnapshot:
    return DataSnapshot.from_columns(columns, units=units, dtypes=dtypes, provenance=SYNTHETIC)


# --------------------------------------------------------------------------
# one small spec per mark type
# --------------------------------------------------------------------------


def mark_line() -> FigureSpec:
    t = np.linspace(0.0, 10.0, 41)
    return FigureSpec(
        title="line: damped oscillation",
        datasets={"d": _snap({"t": t, "x": np.exp(-0.3 * t) * np.cos(2 * t)},
                             units={"t": "s", "x": "mm"})},
        layers=[LineMark(data="d", encoding={"x": "t", "y": "x"}, name="displacement")],
        axes={"x": Axis(label="Time"), "y": Axis(label="Displacement")},
        annotations=[ReferenceLine(axis="y", value=0.0, label="rest")],
    )


def mark_scatter() -> FigureSpec:
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, 40)
    y = 2.0 * x + rng.normal(0, 2.0, 40)
    return FigureSpec(
        title="scatter: coloured by a third value",
        datasets={"d": _snap({"x": x, "y": y, "w": np.abs(y - 2.0 * x)},
                             units={"x": "V", "y": "mA", "w": "mA"})},
        layers=[ScatterMark(data="d", encoding={"x": "x", "y": "y",
                                                "color": Encoding("w", title="residual")})],
        axes={"x": Axis(label="Voltage"), "y": Axis(label="Current")},
    )


def mark_area() -> FigureSpec:
    h = np.arange(0, 25, 1.0)
    return FigureSpec(
        title="area: power over a day",
        datasets={"d": _snap({"hour": h, "p": np.clip(np.sin((h - 6) / 12 * np.pi), 0, None)
                              * 4.0}, units={"hour": "h", "p": "kW"})},
        layers=[AreaMark(data="d", encoding={"x": "hour", "y": "p"}, name="solar output")],
        axes={"x": Axis(label="Hour of day", limits=[0, 24]), "y": Axis(label="Power")},
    )


def mark_bar() -> FigureSpec:
    return FigureSpec(
        title="bar: one category has no data",
        datasets={"d": _snap({"material": ["steel", "copper", "glass", "oak"],
                              "k": [50.2, 401.0, None, 0.17]},
                             units={"k": "W/(m·K)"})},
        layers=[BarMark(data="d", encoding={"x": "material", "y": "k"})],
        axes={"x": Axis(label="Material"), "y": Axis(label="Thermal conductivity",
                                                     scale="log")},
        caption="Glass was not measured in this set; its slot says so instead of showing 0.",
    )


def mark_histogram() -> FigureSpec:
    rng = np.random.default_rng(1)
    return FigureSpec(
        title="histogram: 500 normal samples",
        datasets={"d": _snap({"v": rng.normal(5.0, 1.5, 500)}, units={"v": "ms"})},
        layers=[HistogramMark(data="d", encoding={"x": "v"}, bins=20)],
        axes={"x": Axis(label="Latency"), "y": Axis(label="Count")},
    )


def mark_heatmap() -> FigureSpec:
    gx, gy = np.meshgrid(np.arange(8.0), np.arange(6.0), indexing="xy")
    temp = 20 + 5 * np.exp(-((gx - 5) ** 2 + (gy - 2) ** 2) / 6)
    return FigureSpec(
        title="heatmap: plate temperature",
        datasets={"d": _snap({"x": gx.ravel(), "y": gy.ravel(), "T": temp.ravel()},
                             units={"x": "cm", "y": "cm", "T": "°C"})},
        layers=[HeatmapMark(data="d", encoding={"x": "x", "y": "y",
                                                "color": Encoding("T", title="temperature")})],
        axes={"x": Axis(label="x"), "y": Axis(label="y")},
    )


def mark_quiver() -> FigureSpec:
    gx, gy = np.meshgrid(np.linspace(-2, 2, 9), np.linspace(-2, 2, 9), indexing="xy")
    return FigureSpec(
        title="quiver: saddle flow",
        datasets={"d": _snap({"x": gx.ravel(), "y": gy.ravel(), "u": gx.ravel(),
                              "v": -gy.ravel(), "speed": np.hypot(gx, gy).ravel()},
                             units={"x": "m", "y": "m", "u": "m/s", "v": "m/s",
                                    "speed": "m/s"})},
        layers=[QuiverMark(data="d", encoding={"x": "x", "y": "y", "u": "u", "v": "v",
                                               "color": "speed"})],
        axes={"x": Axis(label="x"), "y": Axis(label="y")},
    )


def mark_function() -> FigureSpec:
    return FigureSpec(
        title="function: exp(-a·x)·sin(x), a = 0.3",
        layers=[FunctionMark(expression="exp(-a*x)*sin(x)", domain={"x": [0.0, 12.0]},
                             samples=200, parameters={"a": 0.3}, role="reference",
                             name="exp(-a x) sin(x)")],
        axes={"x": Axis(label="x", unit="rad"), "y": Axis(label="f(x)")},
    )


MARKS = {
    "mark-line": mark_line,
    "mark-scatter": mark_scatter,
    "mark-area": mark_area,
    "mark-bar": mark_bar,
    "mark-histogram": mark_histogram,
    "mark-heatmap": mark_heatmap,
    "mark-quiver": mark_quiver,
    "mark-function": mark_function,
}


# --------------------------------------------------------------------------
# templates and the legacy bridge
# --------------------------------------------------------------------------


def template_specs() -> dict:
    """Every registered template's own example payload, built as-is."""
    out = {}
    for entry in templates.list_templates():
        t = templates.get(entry["name"])
        out[f"template-{t.name}"] = t.build_payload(t.example())
    return out


#: Themes the reporting templates are also shown in (entry suffix -> theme).
THEMED = {"report-light": "muutto365-report-light", "admin-dark": "muutto365-admin-dark"}
THEMED_TEMPLATES = ("bar_comparison", "funnel", "timeseries")


def themed_specs() -> dict[str, FigureSpec]:
    """Reporting template examples under each Muutto365 theme. A template
    with a ``theme`` parameter is built with it (so text it writes uses the
    theme's number format); otherwise the built spec's theme is set."""
    out = {}
    for name in THEMED_TEMPLATES:
        t = templates.get(name)
        for suffix, theme in THEMED.items():
            payload = t.example()
            if t.parameter("theme") is not None:
                payload["params"] = dict(payload["params"], theme=theme)
                spec = t.build_payload(payload)
            else:
                spec = t.build_payload(payload)
                spec.theme = theme
            out[f"template-{name}--{suffix}"] = spec
    return out


def legacy_specs() -> dict[str, FigureSpec]:
    x, y = sp.symbols("x y")
    viz = Visualizer(N=200, colormap="viridis")
    return {
        "legacy-function-2d": viz.to_spec(lambda t: np.sin(t) / (1 + t**2), dim=2,
                                          domain=(-6, 6), title="visualize(dim=2)"),
        "legacy-surface": viz.to_spec(sp.sin(x) * sp.cos(y), dim=3, n=40, domain=(-3, 3),
                                      title="visualize(dim=3)"),
        "legacy-field-2d": viz.to_spec([-y, x], dim=2, kind="vector_field", domain=(-2, 2),
                                       n=15, units={"x": "m", "y": "m", "vector": "m/s"},
                                       title="visualize_vf(dim=2)"),
        "legacy-field-3d": viz.to_spec([-y, x, 0.3 * sp.Symbol("z")], dim=3,
                                       kind="vector_field", domain=(-2, 2), n=5,
                                       title="visualize_vf(dim=3)"),
    }


# --------------------------------------------------------------------------
# networks (NetworkSpec; the templates above cover org_chart, dependency_map
# and network)
# --------------------------------------------------------------------------


def _communities(layout: Layout) -> NetworkSpec:
    """Seeded stochastic block model: 3 communities of 12, dense inside,
    sparse between; integer weights, two of them not recorded."""
    rng = np.random.default_rng(7)
    nodes = [Node(f"{g}{i}", group=f"community {g}") for g in "ABC" for i in range(1, 13)]
    edges = []
    for a in range(len(nodes)):
        for b in range(a + 1, len(nodes)):
            same = nodes[a].group == nodes[b].group
            if rng.random() < (0.35 if same else 0.025):
                edges.append(Edge(nodes[a].id, nodes[b].id,
                                  weight=float(rng.integers(1, 10 if same else 3))))
    edges[3].weight = edges[20].weight = None
    return NetworkSpec(nodes=nodes, edges=edges, layout=layout,
                       edge_weight=Quantity("Interactions", "count/day"),
                       title="Stochastic block model (synthetic, seed 7)",
                       caption="Synthetic graph; two edges have no recorded count.")


def _bipartite() -> NetworkSpec:
    people = [("p1", "Aino"), ("p2", "Bo"), ("p3", "Chen"), ("p4", "Dara")]
    projects = ["Pricing", "Routing", "Reporting"]
    alloc = [("p1", "Pricing", 0.6), ("p1", "Routing", 0.4), ("p2", "Routing", 0.5),
             ("p3", "Reporting", 0.8), ("p4", "Pricing", 0.2), ("p4", "Reporting", 0.8)]
    return NetworkSpec(
        nodes=[Node(i, label=n, group="person") for i, n in people]
        + [Node(p, group="project") for p in projects],
        edges=[Edge(a, b, weight=w) for a, b, w in alloc],
        edge_weight=Quantity("Allocation", "FTE"),
        layout=Layout("bipartite"), title="People and projects (illustrative)")


def network_specs() -> dict[str, NetworkSpec]:
    return {
        "network-force": _communities(Layout("force", seed=3)),
        "network-matrix-group": _communities(Layout("matrix", order="group")),
        "network-matrix-seriation": _communities(Layout("matrix", order="seriation")),
        "network-circular": _communities(Layout("circular")),
        "network-bipartite": _bipartite(),
    }


def all_specs() -> dict:
    specs = {name: build() for name, build in MARKS.items()}
    specs.update(template_specs())
    specs.update(themed_specs())
    specs.update(legacy_specs())
    specs.update(network_specs())
    return specs


VIEWS = {
    "view-reporting-pipeline": reporting_pipeline,
    "view-weekly-briefing": weekly_briefing,
}
VIEW_FORMATS = ("html", "svg", "png")


def export_views(out: Path, clock) -> dict:
    """Export every gallery view; returns ``{name: ViewArtifact}``."""
    artifacts = {}
    for name, build in VIEWS.items():
        artifacts[name] = export_view(build(), VIEW_FORMATS, FlatWriter(out, name), name=name,
                                      clock=clock)
        warns = sorted({w["code"] for v in artifacts[name].variants for w in v.warnings})
        print(f"{name:28s} {artifacts[name].artifact_id}  warnings: {', '.join(warns) or '-'}")
    return artifacts


def main(out: Path | None = None, clock=None) -> dict:
    """Export every gallery entry to ``out`` (default ``examples/out``);
    returns ``{name: VisualizationArtifact}``."""
    out = Path(out) if out is not None else OUT
    out.mkdir(parents=True, exist_ok=True)
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    artifacts = {}
    for name, spec in all_specs().items():
        exporter = export_network if isinstance(spec, NetworkSpec) else export
        artifacts[name] = exporter(spec, FORMATS, FlatWriter(out, name), request=REQUEST,
                                 name=name, clock=clock)
        warns = sorted({w["code"] for v in artifacts[name].variants for w in v.warnings})
        print(f"{name:28s} {artifacts[name].artifact_id}  warnings: {', '.join(warns) or '-'}")
    views = export_views(out, clock)
    print(f"wrote {len(artifacts)} figures and {len(views)} views to {out}")
    return artifacts


if __name__ == "__main__":
    main()
