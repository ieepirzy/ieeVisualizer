"""Domain generation shared by function and vector-field plotting.

A *domain* here is the set of input coordinates a function is sampled on.
Each axis is sampled either evenly (``numpy.linspace``) or uniformly at random;
random samples are sorted so that line plots and grids stay monotone and
``plot_surface``/``quiver`` receive a structured (if irregular) grid.
"""

from collections.abc import Sequence
from typing import Optional, Union

import numpy as np

Bounds = tuple[float, float]
RngLike = Union[None, int, np.random.Generator]


def as_rng(rng: RngLike) -> np.random.Generator:
    """Return a ``numpy.random.Generator`` for ``None``, an int seed or a Generator."""
    if isinstance(rng, np.random.Generator):
        return rng
    return np.random.default_rng(rng)


def int_seed(rng: RngLike) -> Optional[int]:
    """The seed when ``rng`` is an int (not a bool), else None."""
    if isinstance(rng, (int, np.integer)) and not isinstance(rng, bool):
        return int(rng)
    return None


def normalize_bounds(domain, n_axes: int) -> tuple[Bounds, ...]:
    """Expand ``domain`` into one ``(lo, hi)`` pair per axis and validate it.

    ``domain`` is either a single ``(lo, hi)`` pair, applied to every axis, or a
    sequence of ``n_axes`` such pairs.
    """
    # datetime64/timedelta64 casts to float silently (as days, seconds, ... since
    # the epoch, depending on the unit); that is not a numeric domain, so refuse it.
    try:
        kind = np.asarray(domain).dtype.kind
    except (TypeError, ValueError):
        kind = "O"
    if kind in "mM":
        raise ValueError(
            f"domain must be numeric (lo, hi) pairs; datetime/timedelta bounds are not "
            f"supported, got {domain!r}"
        )
    try:
        arr = np.asarray(domain, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"domain must be numeric (lo, hi) pairs, got {domain!r}") from exc

    if arr.shape == (2,):
        pairs = np.tile(arr, (n_axes, 1))
    elif arr.shape == (n_axes, 2):
        pairs = arr
    else:
        raise ValueError(
            f"domain must be a (lo, hi) pair or {n_axes} such pairs, got shape {arr.shape}"
        )

    for lo, hi in pairs:
        if not (np.isfinite(lo) and np.isfinite(hi)):
            raise ValueError(f"domain bounds must be finite, got ({lo}, {hi})")
        if not lo < hi:
            raise ValueError(f"domain bounds must satisfy lo < hi, got ({lo}, {hi})")
    return tuple((float(lo), float(hi)) for lo, hi in pairs)


def sample_axis(
    bounds: Bounds, n: int, *, linspace: bool, rng: Optional[np.random.Generator] = None
) -> np.ndarray:
    """Sample one axis with ``n`` points, evenly spaced or sorted uniform random."""
    if int(n) != n or n < 2:
        raise ValueError(f"number of samples per axis must be an integer >= 2, got {n!r}")
    n = int(n)
    lo, hi = bounds
    if linspace:
        return np.linspace(lo, hi, n)
    gen = rng if rng is not None else np.random.default_rng()
    return np.sort(gen.uniform(lo, hi, n))


def make_grid(
    bounds: Sequence[Bounds],
    n: int,
    *,
    linspace: bool,
    rng: Optional[np.random.Generator] = None,
) -> tuple[np.ndarray, ...]:
    """Return ``numpy.meshgrid(..., indexing="xy")`` arrays over ``bounds``.

    With one axis this is just the sampled 1-D axis (wrapped in a tuple).
    """
    axes = [sample_axis(b, n, linspace=linspace, rng=rng) for b in bounds]
    if len(axes) == 1:
        return (axes[0],)
    return tuple(np.meshgrid(*axes, indexing="xy"))
