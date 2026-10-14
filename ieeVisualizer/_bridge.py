"""Legacy bridge: turn what ``Visualizer.visualize``/``visualize_vf`` sample
into a :class:`~ieeVisualizer.spec.FigureSpec`.

The legacy calls draw straight into Matplotlib axes (they accept ``ax=``,
``style=``, ``streamplot=`` and colour 3D arrows by magnitude), so they keep
drawing directly; see ``Visualizer.to_spec`` for why they are not rerouted
through the adapter. This module builds the *declarative* equivalent from the
same samples (``Visualizer._sample_function`` / ``_sample_field``): the
sampled values are stored as an inline :class:`DataSnapshot` whose provenance
records how they were produced, so the figure can be rendered, exported with
a manifest and re-rendered later from the frozen samples without the original
Python callable.

Spec shape produced:

* ``kind="function"``, ``dim=2``: dataset ``samples`` with columns ``x`` and
  ``f``; one sampled ``function`` mark (drawn as a line).
* ``kind="function"``, ``dim=3``: columns ``x``, ``y``, ``f`` on the full grid
  in ``cartesian3d``; one sampled ``function`` mark (drawn as a surface).
* ``kind="vector_field"``: columns ``x``, ``y`` [``z``], ``u``, ``v`` [``w``]
  and ``magnitude``; one ``quiver`` mark. In 2D arrows are coloured by
  ``magnitude`` (with a colourbar, as ``visualize_vf`` does); in 3D they are
  scaled so the longest arrow spans one grid cell (the same rule as
  ``visualize_vf``) but drawn in one colour, because the spec's 3D quiver has
  no colour channel.

Column names are fixed (the user's symbol names go into the axis labels), so
a symbol called ``u`` or ``magnitude`` cannot collide with a column.

A point where the function is non-finite is stored as ``null`` (the legacy
call leaves it out of the plot with a ``RuntimeWarning``; the renderer draws
it as a gap or hole and says so in a ``null_values_omitted`` warning).

The layer ``role`` defaults to ``"reference"``, the spec's role for an
analytic curve and the default the ``vector_field_2d`` template uses for
expressions: an evaluated formula is not a measurement. The renderer draws
reference layers lighter; pass ``role="observed"`` for full-strength styling.
"""

from __future__ import annotations

import numpy as np

from .spec import (
    Axis,
    Column,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    Legend,
    Provenance,
    QuiverMark,
)

KINDS = ("function", "vector_field")
SOURCE = "ieeVisualizer.Visualizer.to_spec"
DATASET = "samples"


def describe_fn(fn) -> dict:
    """A stable, JSON-safe description of the user's input for provenance.

    SymPy input is recorded as its expression string(s). A Python callable is
    recorded by ``module.qualname`` (e.g. ``numpy.cos``, ``__main__.<lambda>``)
    and not by ``repr()``, which embeds a memory address and would make the
    spec hash differ between runs. Source code is not captured: it may not be
    available and is not needed to reproduce the figure, because the samples
    themselves are stored.
    """
    import sympy as sp

    if isinstance(fn, sp.Basic):
        return {"type": "sympy", "expression": str(fn)}
    if isinstance(fn, (list, tuple, sp.MatrixBase)) and any(
            isinstance(c, sp.Basic) for c in fn):
        return {"type": "sympy", "expression": [str(sp.sympify(c)) for c in fn]}
    module = getattr(fn, "__module__", None) or type(fn).__module__
    if isinstance(fn, np.ufunc):
        module = "numpy"
    name = (getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
            or type(fn).__qualname__)
    return {"type": "callable", "callable": f"{module}.{name}"}


def _seed(rng):
    """The per-call seed when one was given as an int, else None."""
    if isinstance(rng, (int, np.integer)) and not isinstance(rng, bool):
        return int(rng)
    return None


def _check_units(units, allowed: tuple) -> dict:
    units = dict(units or {})
    unknown = sorted(set(units) - set(allowed))
    if unknown:
        raise ValueError(f"units= keys must be among {', '.join(allowed)} for this plot; "
                         f"got {', '.join(map(repr, unknown))}")
    for k, v in units.items():
        if v is not None and not isinstance(v, str):
            raise ValueError(f"units[{k!r}] must be a string or None, got {v!r}")
    return units


def _values(arr) -> list:
    """Row-major list with masked (non-finite) points as None."""
    flat = np.ma.asarray(arr).ravel()
    data = np.asarray(np.ma.getdata(flat), dtype=float)
    mask = np.ma.getmaskarray(flat)
    return [None if m else float(v) for v, m in zip(data.tolist(), mask.tolist())]


def _provenance(kind, fn, s, symbols_given, rng, seed_note) -> Provenance:
    extra = {
        "kind": kind,
        "dim": s.dim,
        "input": describe_fn(fn),
        "variables": list(s.labels),
        "symbols_given": symbols_given,
        "domain": [list(b) for b in s.bounds],
        "n": int(s.n),
        "sampling": "linspace" if s.linspace else "uniform_random_sorted",
        "seed": None if s.linspace else _seed(rng),
        "grid_order": _GRID_ORDER[len(s.grid)],
        "nonfinite_points": int(np.ma.getmaskarray(s.comps[0]).sum()),
    }
    notes = "Sampled by ieeVisualizer.Visualizer from a function; computed, not measured."
    if not s.linspace and extra["seed"] is None:
        notes += " " + seed_note
    return Provenance(source=SOURCE, notes=notes, extra=extra)


_GRID_ORDER = {
    1: "ascending x",
    2: "row-major: y outer, x inner (numpy.meshgrid indexing='xy')",
    3: "row-major: y outer, then x, then z inner (numpy.meshgrid indexing='xy')",
}
_SEED_NOTE = ("Random sample positions came from a generator without a per-call integer "
              "seed, so they cannot be redrawn; the stored samples are the record.")


def function_spec(viz, fn, *, dim, linspace=None, domain=(-5, 5), n=None, symbols=None,
                  rng=None, title=None, units=None, role="reference",
                  colormap=None) -> FigureSpec:
    s = viz._sample_function(fn, dim, linspace, domain, n, symbols, rng)
    axes_keys = ("x", "y") if s.dim == 2 else ("x", "y", "z")
    units = _check_units(units, axes_keys)
    labels = s.labels
    out_axis = axes_keys[-1]
    out_label = f"f({', '.join(labels)})"
    desc = describe_fn(fn)
    out_desc = (f"{out_label} = {desc['expression']}" if desc["type"] == "sympy"
                else f"{out_label} from {desc['callable']}")

    cols = {"x": Column(_values(s.grid[0]), dtype="float", unit=units.get("x"),
                        description=labels[0])}
    encoding = {"x": "x"}
    if s.dim == 3:
        cols["y"] = Column(_values(s.grid[1]), dtype="float", unit=units.get("y"),
                           description=labels[1])
        encoding["y"] = "y"
    cols["f"] = Column(_values(s.comps[0]), dtype="float", unit=units.get(out_axis),
                       description=out_desc)
    encoding[out_axis] = "f"

    style = {"colormap": colormap or viz.colormap} if s.dim == 3 else {}
    axes = {k: Axis(label=lab) for k, lab in zip(axes_keys, [*labels, out_label])}
    return FigureSpec(
        title=title,
        coordinate_system="cartesian2d" if s.dim == 2 else "cartesian3d",
        datasets={DATASET: DataSnapshot(
            columns=cols,
            provenance=_provenance("function", fn, s, symbols is not None, rng, _SEED_NOTE))},
        layers=[FunctionMark(data=DATASET, encoding=encoding, role=role, name=out_label,
                             style=style)],
        axes=axes,
        legend=_legend(),
        metadata={"legacy": {"method": "visualize", "dim": s.dim}},
    )


def field_spec(viz, fn, *, dim, linspace=None, domain=(-5, 5), n=None, symbols=None,
               rng=None, title=None, units=None, role="reference",
               colormap=None) -> FigureSpec:
    from .core import field_magnitude

    s = viz._sample_field(fn, dim, linspace, domain, n, symbols, rng)
    pos = ("x", "y", "z")[: s.dim]
    comp = ("u", "v", "w")[: s.dim]
    units = _check_units(units, (*pos, "vector"))
    vu = units.get("vector")
    magnitude = field_magnitude(s.comps)

    cols = {}
    for k, g, lab in zip(pos, s.grid, s.labels):
        cols[k] = Column(_values(g), dtype="float", unit=units.get(k), description=lab)
    for k, c in zip(comp, s.comps):
        cols[k] = Column(_values(c), dtype="float", unit=vu,
                         description=f"{k}: field component along {s.labels[comp.index(k)]}")
    cols["magnitude"] = Column(_values(magnitude), dtype="float", unit=vu,
                               description="|F|, Euclidean norm of the field components "
                                           "(derived from the sampled components)")
    encoding = {k: k for k in (*pos, *comp)}
    style = {}
    scale = None
    if s.dim == 2:
        encoding["color"] = "magnitude"
        style["colormap"] = colormap or viz.colormap
    else:
        # visualize_vf: the longest arrow spans one grid cell.
        mags = np.ma.compressed(magnitude)
        mmax = float(mags.max())
        cell = min(hi - lo for lo, hi in s.bounds) / (s.n - 1)
        scale = cell / mmax if mmax > 0 else 1.0
    return FigureSpec(
        title=title,
        coordinate_system="cartesian2d" if s.dim == 2 else "cartesian3d",
        datasets={DATASET: DataSnapshot(
            columns=cols,
            provenance=_provenance("vector_field", fn, s, symbols is not None, rng,
                                   _SEED_NOTE))},
        layers=[QuiverMark(data=DATASET, encoding=encoding, role=role, scale=scale,
                           style=style)],
        axes={k: Axis(label=lab) for k, lab in zip(pos, s.labels)},
        legend=_legend(),
        metadata={"legacy": {"method": "visualize_vf", "dim": s.dim}},
    )


def _legend():
    # One layer; the axis labels already say what is drawn.
    return Legend(show=False)
