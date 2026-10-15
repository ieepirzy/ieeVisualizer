"""Legacy convenience API: one call from a function to a Matplotlib figure."""

import contextlib

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from ._domain import as_rng, make_grid, normalize_bounds
from ._evaluate import as_symbol_tuple, evaluate, to_numeric

_SUPPORTED_DIMS = (2, 3)

# Default samples per axis for vector fields. N (default 200) is a sensible
# resolution for a line or surface, but 200x200 arrows are unreadable and
# 200**3 is millions of 3D arrows, so fields default to a coarser grid unless
# ``n=`` is passed explicitly.
_VF_DEFAULT_N = {2: 20, 3: 8}


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
        dim = _check_dim(dim)
        n_inputs = dim - 1
        func = to_numeric(fn, n_inputs, symbols=symbols)
        bounds = normalize_bounds(domain, n_inputs)
        linspace, gen = self._sampling(linspace, rng)
        grid = make_grid(bounds, self.N if n is None else n, linspace=linspace, rng=gen)
        (values,) = evaluate(func, grid)
        labels = _symbol_names(fn, symbols, n_inputs, ("x", "y"))

        with self._style_context():
            fig, ax = self._axes(ax, three_d=(dim == 3))
            if dim == 2:
                ax.plot(grid[0], values)
                ax.set_xlabel(labels[0])
                ax.set_ylabel(f"f({labels[0]})")
            else:
                ax.plot_surface(grid[0], grid[1], values, cmap=self._cmap())
                ax.set_xlabel(labels[0])
                ax.set_ylabel(labels[1])
                ax.set_zlabel(f"f({labels[0]}, {labels[1]})")
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

        Arrow colour encodes field magnitude (``colormap``); 2D plots get a
        colorbar for it. 3D arrows are scaled so the longest one spans one grid
        cell. Points where any component is non-finite are left out with a
        ``RuntimeWarning``; if none is finite, ``ValueError`` is raised.

        :return: the ``matplotlib.figure.Figure`` drawn into
        """
        dim = _check_dim(dim)
        linspace, gen = self._sampling(linspace, rng)
        if streamplot and dim != 2:
            raise ValueError("streamplot=True is only supported for dim=2")
        if streamplot and not linspace:
            raise ValueError("streamplot=True needs an evenly spaced grid (linspace=True)")

        func = to_numeric(fn, dim, n_outputs=dim, symbols=symbols)
        bounds = normalize_bounds(domain, dim)
        if n is None:
            n = min(self.N, _VF_DEFAULT_N[dim])
        grid = make_grid(bounds, n, linspace=linspace, rng=gen)
        comps = evaluate(func, grid, n_outputs=dim)
        magnitude = np.ma.sqrt(sum(c**2 for c in comps))
        labels = _symbol_names(fn, symbols, dim, ("x", "y", "z"))
        cmap = self._cmap()

        with self._style_context():
            fig, ax = self._axes(ax, three_d=(dim == 3))
            if dim == 2:
                q = ax.quiver(grid[0], grid[1], comps[0], comps[1], magnitude, cmap=cmap)
                fig.colorbar(q, ax=ax, label="magnitude")
                if streamplot:
                    ax.streamplot(
                        grid[0][0, :],
                        grid[1][:, 0],
                        comps[0],
                        comps[1],
                        color="0.4",
                        linewidth=0.8,
                        density=1.0,
                    )
                ax.set_aspect("equal", adjustable="box")
            else:
                self._quiver3d(ax, grid, comps, magnitude, bounds, n, cmap)
                ax.set_zlabel(labels[2])
            ax.set_xlabel(labels[0])
            ax.set_ylabel(labels[1])
        if show:
            plt.show()
        return fig

    @staticmethod
    def _quiver3d(ax, grid, comps, magnitude, bounds, n, cmap):
        keep = ~np.ma.getmaskarray(magnitude)
        pts = [g[keep] for g in grid]
        vecs = [np.ma.getdata(c)[keep] for c in comps]
        mags = np.ma.getdata(magnitude)[keep]
        mmax = float(mags.max())
        cell = min(hi - lo for lo, hi in bounds) / (n - 1)
        length = cell / mmax if mmax > 0 else 1.0
        norm = matplotlib.colors.Normalize(vmin=float(mags.min()), vmax=mmax)
        colors = cmap(norm(mags))
        # Axes3D.quiver emits all shafts, then all left head strokes, then all
        # right head strokes; colour each of the three by its arrow's magnitude.
        colors = np.concatenate([colors, colors, colors])
        ax.quiver(*pts, *vecs, length=length, normalize=False, colors=colors)
