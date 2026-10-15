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
  line 15 (12.8), scatter 10 (9.2), area 60 (23-54), bar 1500 (1490),
  heatmap cell 16 (16), quiver arrow 16 (15), histogram input row 1 (0.3)
  plus ``MPL_BIN_US`` 1.6 ms per bin (1.6). Polar and 3D figures use
  ``MPL_POINT_US_OTHER`` (polar line 60 (55), 3D scatter 15 (14), 3D
  function sample 2 (1.05)), else 4x the cartesian cost.

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
                                  "bar": 1_500, "heatmap": 16, "quiver": 16, "histogram": 1}
MPL_POINT_US_OTHER: dict[str, float] = {"function": 2, "line": 60, "scatter": 15, "quiver": 16}
TEMPLATE_LAYERS = 2

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
        return n_states * per_state
    framed = None
    if isinstance(tl, FrameColumn) and isinstance(figure.datasets.get(tl.dataset), DataSnapshot) \
            and tl.column in figure.datasets[tl.dataset].columns:
        framed = frame_rows(figure.datasets[tl.dataset], tl)
    total = 0.0
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
    return n_frames * per_frame
