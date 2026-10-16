"""Render-work estimate behind the ``animation_too_expensive`` guard: a cheap
early refusal, NOT the time guarantee (that is the render deadline,
:mod:`ieeVisualizer.animation.deadline`; any dimension this does not model
can make it wrong by orders of magnitude).

Frame and pixel guards bound how many frames there are and how large; this
estimates what each state costs, from counts alone (states, layers, series
groups, legend entries, points, annotations, frame area), before any state
is built. Units are microseconds of render time measured on the reference
machine (a shared 4-core Linux container, Matplotlib Agg, GIF output,
``axis_limits="fixed"``); each constant is the measured marginal, rounded up:

* per state ``MPL_STATE_US`` 120 ms plus ``MPL_PIXEL_US`` per frame pixel;
* ``MPL_LAYER_US`` per layer; ``MPL_GROUP_US`` per ``series`` or categorical
  ``color`` group (its own artist and legend entry); ``MPL_LAYER_LEGEND_US``
  15 ms per legend entry of an ungrouped layer when a legend is drawn
  (measured 14 ms with 40 function layers);
* per point by mark type (``MPL_POINT_US``; ``MPL_POINT_US_OTHER`` in polar
  and 3D), plus ``MPL_BIN_US`` per histogram bin;
* ``MPL_CATEGORY_US`` per categorical tick label; per annotation
  ``MPL_ANNOTATION_US`` plus ``MPL_LEGEND_ENTRY_US`` for a labelled line or band;
* a template sweep's build per state (:func:`template_build_us`): per syntax
  node of ``u``/``v``, per ``Min``/``Max`` argument and nested pair, per grid
  point.

A frame column state counts only the rows it shows; a template sweep is
estimated before the template runs (2 layers, its data rows and grid
points). ``MAX_RENDER_WORK_US`` = 60 s. ``tests/animation/test_anim_cost_calibration.py``
(``-m slow``) renders one case per term and requires measured / estimated
within [1/6, 1.5].
"""

from __future__ import annotations

from typing import Any

from ..spec import DataSnapshot, FigureSpec, FunctionMark
from .spec import AnimationSpec, FrameColumn, ParameterSweep, TemplateTarget

MAX_RENDER_WORK_US = 60_000_000

MPL_STATE_US = 120_000
MPL_PIXEL_US = 0.2
MPL_LAYER_US = 9_500
MPL_GROUP_US = 14_000
MPL_BIN_US = 1_600
MPL_POINT_US: dict[str, float] = {"function": 1, "line": 15, "scatter": 10, "area": 60,
                                  "bar": 1_200, "heatmap": 110, "quiver": 40, "histogram": 1}
MPL_POINT_US_OTHER: dict[str, float] = {"function": 2, "line": 60, "scatter": 45, "quiver": 40}
MPL_CATEGORY_US = 1_500
MPL_ANNOTATION_US: dict[str, float] = {"text": 9_000, "reference_line": 1_000,
                                       "reference_band": 2_000}
MPL_LEGEND_ENTRY_US = 12_000
MPL_LAYER_LEGEND_US = 15_000
TEMPLATE_LAYERS = 2
TEMPLATE_NODE_US = 700
TEMPLATE_MINMAX_ARG_US = 10_000
TEMPLATE_MINMAX_NEST_US = 1_000
TEMPLATE_GRID_US = 35


def _point_us(spec: FigureSpec, mark_type: str) -> float:
    if spec.coordinate_system == "cartesian2d":
        return MPL_POINT_US.get(mark_type, 15)
    return MPL_POINT_US_OTHER.get(mark_type, 4 * MPL_POINT_US.get(mark_type, 15))


def _rows(ds: Any) -> int:
    return max(ds.lengths().values(), default=0) if isinstance(ds, DataSnapshot) else 0


def _groups(spec: FigureSpec, m) -> int:
    """Distinct values of the column that splits a layer into series."""
    ch = "series" if "series" in m.encoding else "color" if "color" in m.encoding else None
    ds = spec.datasets.get(m.data) if m.data is not None else None
    if ch is None or not isinstance(ds, DataSnapshot):
        return 0
    col = ds.columns.get(m.encoding[ch].field)
    if col is None or (ch == "color" and col.dtype == "float"):
        return 0
    if ch == "color" and m.type == "scatter" and col.dtype == "int":
        return 0  # a continuous colour scale, one artist
    return len({v for v in col.values if v is not None})


def _function_points(spec: FigureSpec, m: FunctionMark) -> int:
    return m.samples ** (2 if spec.coordinate_system == "cartesian3d" else 1)


def _layer_parts(spec: FigureSpec, m) -> tuple[float, float]:
    """``(fixed us per state, us per data row)`` of one layer."""
    fixed = MPL_LAYER_US + MPL_GROUP_US * _groups(spec, m)
    if isinstance(m, FunctionMark) and m.expression is not None:
        return fixed + _function_points(spec, m) * _point_us(spec, "function"), 0.0
    if m.type == "histogram":
        nbins = (len(m.bin_edges) - 1 if m.bin_edges else m.bins) or 10
        fixed += MPL_BIN_US * nbins
    if m.type == "heatmap":  # the grid is allocated and drawn whole: distinct x x y
        ds = spec.datasets.get(m.data)
        cols = [ds.columns.get(m.encoding[a].field) if isinstance(ds, DataSnapshot)
                and a in m.encoding else None for a in ("x", "y")]
        if all(c is not None for c in cols):
            fixed += _point_us(spec, "heatmap") * len(set(cols[0].values)) \
                * len(set(cols[1].values))
    return fixed, _point_us(spec, m.type)


def categories(spec: FigureSpec) -> int:
    """Distinct values on categorical (``str``/``bool``) position axes, summed
    over the axes: each is a tick label drawn in every state (counted over
    the whole dataset, so a frame column showing a subset is over-counted)."""
    seen: dict[str, set] = {}
    for m in spec.layers:
        ds = spec.datasets.get(m.data) if getattr(m, "data", None) is not None else None
        if not isinstance(ds, DataSnapshot):
            continue
        for axis in ("x", "y", "z"):
            enc = m.encoding.get(axis) if isinstance(m.encoding, dict) else None
            col = ds.columns.get(enc.field) if enc is not None else None
            if col is not None and col.dtype in ("str", "bool"):
                seen.setdefault(axis, set()).update(v for v in col.values if v is not None)
    return sum(len(v) for v in seen.values())


def annotations_us(spec: FigureSpec) -> float:
    """Per-state cost of the figure's annotations (a labelled reference line
    or band also adds a legend entry)."""
    total = 0.0
    for a in spec.annotations if isinstance(spec.annotations, list) else []:
        total += MPL_ANNOTATION_US.get(getattr(a, "type", ""), MPL_ANNOTATION_US["text"])
        if getattr(a, "type", "") != "text" and getattr(a, "label", None) is not None:
            total += MPL_LEGEND_ENTRY_US
    return total


def frame_rows(snapshot: DataSnapshot, tl: FrameColumn, index=None) -> list[int]:
    """Rows shown in each frame-column state, in state order (``index``: the
    column's :class:`~ieeVisualizer.animation.frames.FrameIndex`, if known)."""
    from .frames import frame_index

    per = (index or frame_index(snapshot, tl.column)).counts()
    out, run = [], 0
    for i, n in enumerate(per):
        if tl.display == "current":
            out.append(n)
        elif tl.display == "cumulative":
            run += n
            out.append(run)
        else:
            run += n - (per[i - tl.window] if i >= tl.window else 0)
            out.append(run)
    return out


def legend_entries(spec: FigureSpec) -> int:
    """Legend entries of layers not split into groups (a group's entry is in
    ``MPL_GROUP_US``); 0 when no legend is drawn (hidden, or one entry)."""
    if not getattr(spec.legend, "show", True):
        return 0
    n = sum(1 for m in spec.layers if not _groups(spec, m))
    return n if n + sum(_groups(spec, m) for m in spec.layers) > 1 else 0


def _template_points(t: TemplateTarget) -> tuple[int, int]:
    """``(data rows, grid points)`` of a template target, before it is built."""
    rows = _rows(t.data)
    grid = t.params.get("grid")
    if grid is None:
        try:
            from .. import templates

            p = templates.get(t.name, t.version).parameter("grid")
            grid = p.default if p is not None else None
        except Exception:  # noqa: BLE001 - an unknown template is reported by validation
            grid = None
    if isinstance(grid, bool):
        return rows, 0
    if isinstance(grid, int):
        return rows, grid * grid
    if isinstance(grid, (list, tuple)) and len(grid) == 2 \
            and all(isinstance(g, int) and not isinstance(g, bool) for g in grid):
        return rows, grid[0] * grid[1]
    return rows, 0


def _expression_us(text: Any) -> float:
    """Estimated SymPy time of one template build for one expression (see
    module doc): per syntax node, plus ``Min``/``Max`` arguments and their
    nesting (SymPy compares the arguments of nested ``Min``/``Max`` pairwise)."""
    import ast

    if not isinstance(text, str):
        return 0.0
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return 0.0  # refused by validation before anything is built
    nodes = args = nested = 0
    for node in ast.walk(tree):
        nodes += 1
        if _is_minmax(node):
            args += len(node.args)
            nested += sum(len(n.args) for n in ast.walk(node) if _is_minmax(n))
    return (TEMPLATE_NODE_US * nodes + TEMPLATE_MINMAX_ARG_US * args
            + TEMPLATE_MINMAX_NEST_US * (nested - args))


def _is_minmax(node: Any) -> bool:
    import ast

    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
        and node.func.id in ("Min", "Max")


def template_build_us(t: TemplateTarget) -> float:
    """Estimated time of one ``build()`` of a template target in expression
    mode (SymPy per expression plus sampling per grid point); 0 for data mode."""
    if t.data is not None or not isinstance(t.params, dict):
        return 0.0
    exprs = [t.params.get(k) for k in ("u", "v")]
    if not any(isinstance(e, str) for e in exprs):
        return 0.0
    return sum(_expression_us(e) for e in exprs) + TEMPLATE_GRID_US * _template_points(t)[1]


def min_work_us(anim: AnimationSpec, n_states: int, n_frames: int) -> float:
    """The estimate at zero frame area: a lower bound for any request (used
    by validation, which does not know the request)."""
    return matplotlib_work_us(anim, (0, 0), n_states, anim.figure)


def matplotlib_work_us(anim: AnimationSpec, size_px: tuple[int, int], n_states: int,
                       figure: FigureSpec | None, index=None) -> float:
    """Estimated Matplotlib render time of ``n_states`` states (see module doc)."""
    w, h = size_px
    per_state = MPL_STATE_US + MPL_PIXEL_US * w * h
    tl = anim.timeline
    if figure is None:  # template sweep: the figure exists only after the template runs
        rows, grid = _template_points(tl.target) if isinstance(tl, ParameterSweep) \
            and isinstance(tl.target, TemplateTarget) else (0, 0)
        data = getattr(tl.target, "data", None) if isinstance(tl, ParameterSweep) else None
        cols = len(data.columns) if isinstance(data, DataSnapshot) else 1
        # every data column may become a layer with a legend entry
        per_state += max(TEMPLATE_LAYERS, cols) * (MPL_LAYER_US + MPL_LAYER_LEGEND_US) \
            + rows * cols * MPL_POINT_US["line"] + grid * MPL_POINT_US["quiver"]
        if isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget):
            per_state += template_build_us(tl.target)
        return n_states * per_state
    framed = None
    if isinstance(tl, FrameColumn) and isinstance(figure.datasets.get(tl.dataset), DataSnapshot) \
            and tl.column in figure.datasets[tl.dataset].columns:
        framed = frame_rows(figure.datasets[tl.dataset], tl, index)
    total = 0.0
    per_state += annotations_us(figure) + MPL_CATEGORY_US * categories(figure) \
        + MPL_LAYER_LEGEND_US * legend_entries(figure)
    for m in figure.layers:
        fixed, per_row = _layer_parts(figure, m)
        if not per_row:
            per_state += fixed
        elif framed is not None and m.data == tl.dataset:
            per_state += fixed
            total += per_row * sum(framed)
        else:
            per_state += fixed + per_row * _rows(figure.datasets.get(m.data))
    return total + n_states * per_state
