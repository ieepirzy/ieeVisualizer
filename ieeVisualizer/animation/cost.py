"""Render-work estimate behind the ``animation_too_expensive`` guard.

Frame and pixel guards bound how many frames an animation has and how large
they are; they do not bound what each state contains. A figure with 1000
function layers, or a 200,000-sample curve under Manim, passes both and then
takes tens of minutes. :func:`render_work_us` estimates the render time of an
animation from counts alone (states, layers, series groups, points, frame
area) before any state is built, and :func:`~ieeVisualizer.animation.base.enforce_limits`
refuses an estimate above the adapter's budget.

Units are microseconds of render time measured on the reference machine (a
shared 4-core Linux container, Matplotlib Agg, GIF output, the default
``axis_limits="fixed"``, which builds every sweep state twice). Each constant
is the measured marginal cost, rounded up:

Matplotlib, per state (every state is drawn once)

* ``MPL_STATE_US`` 120 ms fixed cost of a state (figure, PNG round trip,
  quantization) plus ``MPL_PIXEL_US`` 0.2 us per frame pixel;
* ``MPL_LAYER_US`` 9.5 ms per layer (measured 9.3 ms);
* ``MPL_GROUP_US`` 14 ms per ``series`` group or categorical ``color`` group
  (each becomes its own artist and legend entry; measured 8.5 and 14 ms);
* per point, by mark type (``MPL_POINT_US``): function sample 1 us (0.24),
  line 15 (12.8), scatter 10 (9.2), area 60 (23-54), bar 1200 (1130),
  heatmap cell 110 (54-83; a numeric heatmap frame column builds every state
  twice for fixed limits), quiver arrow 40 (15 per draw; 63-83 per template
  grid point with the build, as a sweep draws every state twice), histogram
  input row 1 (0.3)
  plus ``MPL_BIN_US`` 1.6 ms per bin (1.6). Polar and 3D figures use
  ``MPL_POINT_US_OTHER`` (polar line 60 (55), 3D scatter 45 (33-41), 3D
  function sample 2 (0.2-0.5)), else 4x the cartesian cost;
* ``MPL_CATEGORY_US`` 1.5 ms per distinct value on a categorical position
  axis (a tick label; 0.4 ms beside a bar, about 1.3 ms on a heatmap axis);
* per annotation (``MPL_ANNOTATION_US``): text 9 ms (7.0-7.8), reference line
  1 ms (0.9), reference band 2 ms (1.4-1.7), plus ``MPL_LEGEND_ENTRY_US`` 12
  ms for a labelled line or band (its legend entry; 9.3-12.2 in all). An
  animation has at most ``MAX_ANIMATION_ANNOTATIONS`` (200) annotations;
* a template sweep's build per state (expression mode, ``template_build_us``):
  ``TEMPLATE_NODE_US`` 0.7 ms per syntax node of ``u`` and ``v`` (SymPy parse,
  bounded rebuild and lambdify; 0.2-0.6 ms measured across expression
  families), ``TEMPLATE_MINMAX_ARG_US`` 10 ms per ``Min``/``Max`` argument
  plus ``TEMPLATE_MINMAX_NEST_US`` 1 ms per pair of nested ``Min``/``Max``
  arguments (SymPy compares them pairwise: 40 alternately nested ``Max``/``Min``
  calls build in 4.3 s), and ``TEMPLATE_GRID_US`` 35 us per grid point
  (sampling, the dry-run in ``check()`` and the snapshot; 18-33). Measured on
  12 expression families at grids 2-200, the estimate is 1.1-5x the build
  time (worst nested same-kind ``Max``, which SymPy flattens).

A frame column state counts only the rows that state shows (``current``,
``cumulative`` or ``window``); every other dataset counts in full in every
state. A template sweep is estimated before the template runs: 2 layers plus
its data rows (line cost) and grid points (quiver cost).

``MAX_RENDER_WORK_US`` = 60 s. Measured wall time of accepted inputs just
under it (estimate / measured): 300 plain states at 640x480 57 / 55 s; 60
states x 91 layers 59 / 61 s; 30 states x 1200 bars 58 / 55 s; 60 states x 60
colour groups 59 / 41 s; 30 states x 120,000 line rows 58 / 18 s; a
cumulative frame column of 100 states over 59,000 rows 58 / 13 s. Over the
budget, 300 states x 1000 layers (about 40 minutes) is refused in
milliseconds.

Manim redraws every output frame (``always_redraw``; holds included) and maps
every curve point through ``Axes.c2p`` in Python, so its budget is per output
frame and stricter per point: ``MANIM_FRAME_US`` 100 ms (70 ms over 300
frames), ``MANIM_LAYER_US`` 2 ms (1.1), ``MANIM_SAMPLE_US`` 50 us per
function sample (46) and ``MANIM_ARROW_US`` 5 ms per vector-field arrow
(2.7-5.6), against the same 60 s. Measured just under it: 300 frames x 1900
samples 59 / 36 s, 95 frames of a 10x10 field 57 / 32 s. A 200,000-sample
curve (about 5 s per frame) is refused before Manim starts.

These are estimates of one machine's speed, not promises: they keep the worst
accepted input near a minute rather than an hour.
``tests/animation/test_anim_cost_calibration.py`` renders one case per term
and requires measured / estimated within [1/6, 1.5]: under by at most half
again (machine noise; a case over the band is timed once more), over by at
most 6x (measured/estimated in full-suite and isolated runs after round 3:
0.59-1.40).

Validation (which does not know the request) refuses a template sweep whose
:func:`min_work_us` (the cheaper cost model at zero frame area) is over the
budget before it checks any value beyond the first.
"""

from __future__ import annotations

from collections import Counter
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
TEMPLATE_LAYERS = 2
TEMPLATE_NODE_US = 700
TEMPLATE_MINMAX_ARG_US = 10_000
TEMPLATE_MINMAX_NEST_US = 1_000
TEMPLATE_GRID_US = 35

MANIM_FRAME_US = 100_000
MANIM_LAYER_US = 2_000
MANIM_SAMPLE_US = 50
MANIM_ARROW_US = 5_000

COST_MODELS = ("matplotlib", "manim")


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


def frame_rows(snapshot: DataSnapshot, tl: FrameColumn) -> list[int]:
    """Rows shown in each frame-column state, in state order."""
    from .frames import _key, frame_keys

    col = snapshot.columns[tl.column]
    counts = Counter(_key(v, col.dtype) for v in col.values if v is not None)
    keys = frame_keys(snapshot, tl.column)[0]
    per = [counts[k] for k in keys]
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
    """The cheapest cost model's estimate at zero frame area: a lower bound of
    every adapter's estimate for any request (used by validation, which does
    not know the request)."""
    return min(matplotlib_work_us(anim, (0, 0), n_states, anim.figure),
               manim_work_us(anim, n_frames))


def matplotlib_work_us(anim: AnimationSpec, size_px: tuple[int, int], n_states: int,
                       figure: FigureSpec | None) -> float:
    """Estimated Matplotlib render time of ``n_states`` states (see module doc)."""
    w, h = size_px
    per_state = MPL_STATE_US + MPL_PIXEL_US * w * h
    tl = anim.timeline
    if figure is None:  # template sweep: the figure exists only after the template runs
        rows, grid = _template_points(tl.target) if isinstance(tl, ParameterSweep) \
            and isinstance(tl.target, TemplateTarget) else (0, 0)
        per_state += TEMPLATE_LAYERS * MPL_LAYER_US + rows * MPL_POINT_US["line"] \
            + grid * MPL_POINT_US["quiver"]
        if isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget):
            per_state += template_build_us(tl.target)
        return n_states * per_state
    framed = None
    if isinstance(tl, FrameColumn) and isinstance(figure.datasets.get(tl.dataset), DataSnapshot) \
            and tl.column in figure.datasets[tl.dataset].columns:
        framed = frame_rows(figure.datasets[tl.dataset], tl)
    total = 0.0
    per_state += annotations_us(figure) + MPL_CATEGORY_US * categories(figure)
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


def manim_work_us(anim: AnimationSpec, n_frames: int) -> float:
    """Estimated Manim render time of ``n_frames`` output frames."""
    per_frame = float(MANIM_FRAME_US)
    tl = anim.timeline
    if anim.figure is not None:
        for m in anim.figure.layers:
            per_frame += MANIM_LAYER_US
            if isinstance(m, FunctionMark) and m.expression is not None:
                per_frame += MANIM_SAMPLE_US * _function_points(anim.figure, m)
    elif isinstance(tl, ParameterSweep) and isinstance(tl.target, TemplateTarget):
        rows, grid = _template_points(tl.target)
        per_frame += MANIM_LAYER_US + MANIM_ARROW_US * (rows + grid)
        # the expressions are parsed and lambdified once for the whole clip
        once = sum(_expression_us(tl.target.params.get(k)) for k in ("u", "v")) \
            if isinstance(tl.target.params, dict) else 0.0
        return once + n_frames * per_frame
    return n_frames * per_frame
