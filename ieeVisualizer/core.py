"""Legacy convenience API: one call from a function to a Matplotlib figure."""

import contextlib
from dataclasses import dataclass

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from ._domain import as_rng, int_seed, make_grid, normalize_bounds
from ._evaluate import as_symbol_tuple, evaluate, to_numeric

_SUPPORTED_DIMS = (2, 3)

# Default samples per axis for vector fields. N (default 200) is a sensible
# resolution for a line or surface, but 200x200 arrows are unreadable and
# 200**3 is millions of 3D arrows, so fields default to a coarser grid unless
# ``n=`` is passed explicitly.
_VF_DEFAULT_N = {2: 20, 3: 8}


@dataclass
class Sampled:
    """What one legacy call sampled: the grid, the masked output component(s)
    (non-finite points masked), axis labels and the sampling settings."""

    dim: int
    grid: tuple
    comps: list
    labels: list
    bounds: tuple
    n: int
    linspace: bool


def field_magnitude(comps):
    """Euclidean norm of the field components (masked where any is masked)."""
    return np.ma.sqrt(sum(c**2 for c in comps))


def _check_dim(dim) -> int:
    if isinstance(dim, bool) or dim not in _SUPPORTED_DIMS:
        raise ValueError(f"dim must be 2 or 3, got {dim!r}")
    return int(dim)


def _symbol_names(fn, symbols, n_inputs, default):
    """Axis labels: SymPy symbol names when known, generic names otherwise."""
    import sympy as sp

    if symbols is not None:
        return [str(s) for s in as_symbol_tuple(symbols)]
    exprs = list(fn) if isinstance(fn, (list, tuple, sp.MatrixBase)) else [fn]
    if any(isinstance(e, sp.Basic) for e in exprs):
        free = set().union(*(sp.sympify(e).free_symbols for e in exprs))
        names = sorted(s.name for s in free)
        if len(names) == n_inputs:
            return names
    return list(default[:n_inputs])


def _legacy_look(spec):
    """The spec ``visualize``/``visualize_vf`` draw: ``to_spec``'s, with the
    legacy calls' full-strength look pinned as style hints. The layer keeps
    role ``reference`` (a computed formula), which the adapter would draw
    lighter; these calls have always drawn opaque, and a 2D curve in the
    style's first cycle colour at its default line width."""
    from dataclasses import replace

    (mark,) = spec.layers
    style = {**mark.style, "opacity": 1.0}
    if mark.type == "function" and spec.coordinate_system == "cartesian2d":
        cycle = matplotlib.rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
        style.update(color=matplotlib.colors.to_hex(cycle[0], keep_alpha=True),
                     linewidth=float(matplotlib.rcParams["lines.linewidth"]))
    return replace(spec, layers=[replace(mark, style=style)])


class Visualizer:
    def __init__(self, *, linspace=True, N=200, style=None, colormap="viridis", rng=None):
        """
        Plot functions and vector fields with Matplotlib.

        :param linspace: default sampling: evenly spaced (True) or sorted uniform random (False)
        :param N: samples per axis for function plots (``visualize``)
        :param style: Matplotlib style name/dict/path applied while drawing, or None
        :param colormap: colormap name used for surfaces and vector-field magnitude
        :param rng: seed or ``numpy.random.Generator`` for random sampling; pass a value
            to make ``linspace=False`` plots reproducible
        """
        self.linspace = linspace
        self.N = N
        self.style = style
        self.colormap = colormap
        self.rng = as_rng(rng)
        # kept for provenance: the generator above is shared by every call
        self.rng_seed = int_seed(rng)

    # -- helpers ---------------------------------------------------------------

    def _style_context(self):
        if self.style is None:
            return contextlib.nullcontext()
        return plt.style.context(self.style)

    def _cmap(self):
        try:
            return matplotlib.colormaps[self.colormap]
        except KeyError:
            raise ValueError(f"unknown colormap {self.colormap!r}") from None

    @staticmethod
    def _axes(ax, three_d):
        if ax is None:
            fig = plt.figure()
            ax = fig.add_subplot(projection="3d" if three_d else None)
            return fig, ax
        if three_d and getattr(ax, "name", None) != "3d":
            raise ValueError("dim=3 needs a 3D axes (created with projection='3d')")
        if not three_d and getattr(ax, "name", None) == "3d":
            raise ValueError("dim=2 needs a 2D axes, got a 3D axes")
        return ax.figure, ax

    def _sampling(self, linspace, rng):
        linspace = self.linspace if linspace is None else linspace
        gen = self.rng if rng is None else as_rng(rng)
        return linspace, gen

    @staticmethod
    def _draw_spec(spec, ax):
        """Draw ``spec`` into ``ax`` with the Matplotlib adapter under the
        caller's style (``draw_into``). Render warnings are re-issued as
        ``RuntimeWarning``, except ``null_values_omitted``: sampling already
        warned about the non-finite points."""
        import warnings

        from .render.mpl import MatplotlibAdapter

        built = MatplotlibAdapter().draw_into(spec, ax)
        for w in built.warnings:
            if w.code != "null_values_omitted":
                warnings.warn(f"{w.code}: {w.message}", RuntimeWarning, stacklevel=3)
        return built

    # One sampling path for drawing (visualize/visualize_vf) and for the spec
    # bridge (to_spec/export), so both see exactly the same samples.

    def _sample_function(self, fn, dim, linspace, domain, n, symbols, rng) -> Sampled:
        dim = _check_dim(dim)
        n_inputs = dim - 1
        func = to_numeric(fn, n_inputs, symbols=symbols)
        bounds = normalize_bounds(domain, n_inputs)
        linspace, gen = self._sampling(linspace, rng)
        n = self.N if n is None else n
        grid = make_grid(bounds, n, linspace=linspace, rng=gen)
        comps = evaluate(func, grid)
        labels = _symbol_names(fn, symbols, n_inputs, ("x", "y"))
        return Sampled(dim, grid, comps, labels, bounds, n, linspace)

    def _sample_field(self, fn, dim, linspace, domain, n, symbols, rng) -> Sampled:
        dim = _check_dim(dim)
        linspace, gen = self._sampling(linspace, rng)
        func = to_numeric(fn, dim, n_outputs=dim, symbols=symbols)
        bounds = normalize_bounds(domain, dim)
        if n is None:
            n = min(self.N, _VF_DEFAULT_N[dim])
        grid = make_grid(bounds, n, linspace=linspace, rng=gen)
        comps = evaluate(func, grid, n_outputs=dim)
        labels = _symbol_names(fn, symbols, dim, ("x", "y", "z"))
        return Sampled(dim, grid, comps, labels, bounds, n, linspace)

    # -- public API ------------------------------------------------------------

    def visualize(
        self,
        fn,
        *,
        dim,
        linspace=None,
        show=False,
        domain=(-5, 5),
        n=None,
        symbols=None,
        ax=None,
        rng=None,
    ):
        """
        Plot ``y = fn(x)`` (dim=2, line) or ``z = fn(x, y)`` (dim=3, surface).

        :param fn: plain callable taking 1 (dim=2) or 2 (dim=3) array arguments, or a
            SymPy expression. Inputs of an expression are its free symbols sorted by
            name unless ``symbols`` is given.
        :param dim: dimensionality of the plot: 2 or 3
        :param linspace: evenly spaced (True) or sorted random (False) samples;
            None uses the constructor's setting
        :param show: call ``plt.show()`` after drawing
        :param domain: ``(lo, hi)`` for every input axis, or one pair per input axis;
            bounds must be real numbers (datetime/timedelta values are rejected)
        :param n: samples per axis; defaults to ``N``
        :param symbols: SymPy symbols, in input order, for an expression; a string is
            parsed like ``sympy.symbols`` (``"x"``, ``"x y"``)
        :param ax: existing Matplotlib axes to draw into (3D axes for dim=3)
        :param rng: seed or Generator overriding the constructor's ``rng`` for this call

        Non-finite results (nan/inf) are left out of the plot with a
        ``RuntimeWarning``; if no result is finite, ``ValueError`` is raised.

        :return: the ``matplotlib.figure.Figure`` drawn into
        """
        from ._bridge import function_spec

        s = self._sample_function(fn, dim, linspace, domain, n, symbols, rng)
        self._cmap()  # an unknown colormap is a ValueError before anything is drawn
        with self._style_context():
            fig, ax = self._axes(ax, three_d=(s.dim == 3))
            spec = function_spec(self, fn, dim=s.dim, symbols=symbols, rng=rng, sampled=s)
            self._draw_spec(_legacy_look(spec), ax)
        if show:
            plt.show()
        return fig

    def visualize_vf(
        self,
        fn,
        *,
        dim,
        linspace=None,
        show=False,
        domain=(-5, 5),
        n=None,
        symbols=None,
        ax=None,
        rng=None,
        streamplot=False,
    ):
        """
        Plot a vector field ``F: R^dim -> R^dim`` as arrows.

        :param fn: callable ``fn(x, y) -> (u, v)`` (dim=2) or ``fn(x, y, z) -> (u, v, w)``
            (dim=3), or a SymPy Matrix / list of ``dim`` expressions. A callable may
            return the components as a tuple/list, as an array stacked along the first
            axis, or (for a constant field) as a 1-D array such as ``np.array([1, 2])``
        :param dim: 2 (``quiver``) or 3 (3D ``quiver``)
        :param linspace: evenly spaced (True) or sorted random (False) grid;
            None uses the constructor's setting
        :param show: call ``plt.show()`` after drawing
        :param domain: ``(lo, hi)`` for every axis, or one pair per axis
        :param n: grid points per axis; defaults to ``min(N, 20)`` in 2D and
            ``min(N, 8)`` in 3D
        :param symbols: SymPy symbols, in input order, for expressions; a string is
            parsed like ``sympy.symbols``
        :param ax: existing Matplotlib axes to draw into (3D axes for dim=3)
        :param rng: seed or Generator overriding the constructor's ``rng`` for this call
        :param streamplot: dim=2 only: also draw streamlines (needs ``linspace=True``)

        Arrow colour encodes field magnitude (``colormap``), with a colorbar
        for it. 3D arrows are scaled so the longest one spans one grid
        cell. Points where any component is non-finite are left out with a
        ``RuntimeWarning``; if none is finite, ``ValueError`` is raised.

        :return: the ``matplotlib.figure.Figure`` drawn into
        """
        dim = _check_dim(dim)
        if streamplot and dim != 2:
            raise ValueError("streamplot=True is only supported for dim=2")
        if streamplot and not (self.linspace if linspace is None else linspace):
            raise ValueError("streamplot=True needs an evenly spaced grid (linspace=True)")

        from ._bridge import field_spec

        s = self._sample_field(fn, dim, linspace, domain, n, symbols, rng)
        self._cmap()  # an unknown colormap is a ValueError before anything is drawn
        with self._style_context():
            fig, ax = self._axes(ax, three_d=(dim == 3))
            spec = field_spec(self, fn, dim=dim, symbols=symbols, rng=rng, sampled=s)
            self._draw_spec(_legacy_look(spec), ax)
            if dim == 2:
                # Spec schema 1.0 has no streamlines and no aspect ratio, so
                # these two legacy options are still drawn directly, on top of
                # the adapter-drawn quiver, from the same samples.
                if streamplot:
                    ax.streamplot(
                        s.grid[0][0, :],
                        s.grid[1][:, 0],
                        s.comps[0],
                        s.comps[1],
                        color="0.4",
                        linewidth=0.8,
                        density=1.0,
                    )
                ax.set_aspect("equal", adjustable="box")
        if show:
            plt.show()
        return fig

    # -- declarative bridge ----------------------------------------------------

    def to_spec(
        self,
        fn,
        *,
        dim,
        kind="function",
        linspace=None,
        domain=(-5, 5),
        n=None,
        symbols=None,
        rng=None,
        title=None,
        units=None,
        role="reference",
    ):
        """
        Build the :class:`~ieeVisualizer.spec.FigureSpec` equivalent of
        ``visualize`` (``kind="function"``) or ``visualize_vf``
        (``kind="vector_field"``).

        ``fn``, ``dim``, ``linspace``, ``domain``, ``n``, ``symbols`` and ``rng``
        mean exactly what they mean for those methods, and the samples come
        from the same code path: with the same arguments and generator state,
        the spec holds the values ``visualize``/``visualize_vf`` would draw.
        They are stored as an inline snapshot (dataset ``"samples"``) whose
        provenance records the input (SymPy expression string, or the
        callable's ``module.qualname``), domain, ``n``, sampling mode, the
        per-call integer seed if one was given, and the number of non-finite
        points (stored as null). Render it with ``ieeVisualizer.render.render``
        or store it with :meth:`export`.

        :param kind: ``"function"`` (line for dim=2, surface for dim=3) or
            ``"vector_field"`` (quiver; 2D arrows coloured by magnitude)
        :param title: optional figure title
        :param units: optional units by axis, shown in the axis labels as
            ``"label (unit)"``. Function plots: ``"x"``, ``"y"`` (and ``"z"``
            for dim=3), where the last axis is the function value. Vector
            fields: ``"x"``, ``"y"`` (``"z"``) for positions and ``"vector"``
            for the components (and magnitude).
        :param role: layer role; ``"reference"`` (default: a computed formula,
            not a measurement, drawn lighter) or ``"observed"``

        Not representable in spec schema 1.0, so not produced here:
        ``streamplot`` streamlines and the equal aspect ratio of 2D fields.
        ``style=``/``ax=``/``show=`` are drawing options of the legacy calls
        and have no spec equivalent.

        ``visualize()``/``visualize_vf()`` draw this same spec (from the same
        single sampling pass) into their own pyplot-managed axes with
        ``MatplotlibAdapter.draw_into``, under ``style=``, with the legacy
        full-strength look pinned as style hints (``opacity`` 1; a 2D curve
        in the style's first cycle colour). Only streamlines and the equal
        aspect of 2D fields are still drawn directly, on top.

        :return: a ``FigureSpec`` that validates without errors
        """
        from ._bridge import KINDS, field_spec, function_spec

        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
        build = function_spec if kind == "function" else field_spec
        return build(self, fn, dim=dim, linspace=linspace, domain=domain, n=n,
                     symbols=symbols, rng=rng, title=title, units=units, role=role)

    def export(
        self,
        fn,
        out,
        *,
        dim,
        kind="function",
        formats=("png", "svg"),
        name=None,
        request=None,
        clock=None,
        **spec_options,
    ):
        """
        Sample ``fn`` like :meth:`to_spec` and export it as PNG/SVG (or PDF)
        files plus ``spec.json`` and a provenance ``manifest.json`` via
        :func:`ieeVisualizer.artifacts.export`.

        :param out: a directory (written as ``<out>/<artifact_id>/...``) or any
            ``ieeVisualizer.artifacts`` writer
        :param formats: formats to render, e.g. ``("png", "svg")``
        :param name: artifact id prefix (default ``"viz"``)
        :param request: ``ieeVisualizer.render.RenderRequest`` for size/dpi/theme
        :param clock: callable returning the ``created_at`` datetime
        :param spec_options: any other :meth:`to_spec` keyword (``linspace``,
            ``domain``, ``n``, ``symbols``, ``rng``, ``title``, ``units``, ``role``)
        :return: the ``VisualizationArtifact`` manifest
        """
        import os

        from .artifacts import LocalDirectoryWriter
        from .artifacts import export as export_artifact

        spec = self.to_spec(fn, dim=dim, kind=kind, **spec_options)
        writer = LocalDirectoryWriter(out) if isinstance(out, (str, os.PathLike)) else out
        return export_artifact(spec, formats, writer, name=name, request=request, clock=clock)
