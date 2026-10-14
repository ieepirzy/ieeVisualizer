"""Turn user input (plain callables or SymPy expressions) into sampled arrays.

The rule enforced here: output that was never computed is not drawn as if it
were. Non-finite samples are masked and reported with a warning; if *no*
sample is finite, evaluation fails instead of producing an empty figure.
"""

import inspect
import warnings
from collections.abc import Sequence
from typing import Callable, Optional

import numpy as np
import sympy as sp


def _is_sympy(obj) -> bool:
    return isinstance(obj, sp.Basic)


def _is_sympy_vector(obj) -> bool:
    if isinstance(obj, sp.MatrixBase):
        return True
    return (
        isinstance(obj, (list, tuple))
        and len(obj) > 0
        and all(isinstance(c, (sp.Basic, int, float)) for c in obj)
        and any(_is_sympy(c) for c in obj)
    )


def as_symbol_tuple(symbols) -> tuple[sp.Symbol, ...]:
    """Normalize a ``symbols=`` argument to a tuple of SymPy symbols.

    Accepts a single Symbol, a string parsed exactly as ``sympy.symbols`` parses
    it (``"x"``, ``"x y"``, ``"x, y"``), or a sequence whose items are Symbols or
    such strings. A string is never iterated character by character.
    """
    if isinstance(symbols, str):
        symbols = sp.symbols(symbols)
    if isinstance(symbols, sp.Symbol):
        return (symbols,)
    out: list[sp.Symbol] = []
    for s in symbols:
        if isinstance(s, str):
            out.extend(as_symbol_tuple(s))
        elif isinstance(s, sp.Symbol):
            out.append(s)
        else:
            raise ValueError(f"symbols= must hold SymPy symbols or names, got {s!r}")
    return tuple(out)


def _resolve_symbols(exprs: Sequence, n_inputs: int, symbols) -> tuple[sp.Symbol, ...]:
    if symbols is not None:
        symbols = as_symbol_tuple(symbols)
        if len(symbols) != n_inputs:
            raise ValueError(f"expected {n_inputs} symbol(s) for this dim, got {len(symbols)}")
        free = set().union(*(sp.sympify(e).free_symbols for e in exprs))
        unbound = free - set(symbols)
        if unbound:
            names = ", ".join(sorted(str(s) for s in unbound))
            raise ValueError(f"expression has free symbol(s) not listed in symbols=: {names}")
        return symbols

    free = set().union(*(sp.sympify(e).free_symbols for e in exprs))
    inferred = tuple(sorted(free, key=lambda s: s.name))
    if len(inferred) != n_inputs:
        names = ", ".join(s.name for s in inferred) or "none"
        raise ValueError(
            f"expression has {len(inferred)} free symbol(s) ({names}) but this dim needs "
            f"{n_inputs}; pass symbols= explicitly to choose the input variables"
        )
    return inferred


def _check_arity(fn: Callable, n_inputs: int) -> None:
    nin = getattr(fn, "nin", None)  # numpy ufuncs have no inspectable signature
    if isinstance(nin, int):
        if nin != n_inputs:
            raise ValueError(f"function takes {nin} argument(s) but this dim needs {n_inputs}")
        return
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return  # cannot tell; let the call itself decide
    try:
        sig.bind(*range(n_inputs))
    except TypeError:
        raise ValueError(
            f"function signature {sig} cannot be called with {n_inputs} positional "
            f"argument(s), which this dim needs"
        ) from None


def to_numeric(fn, n_inputs: int, *, n_outputs: Optional[int] = None, symbols=None) -> Callable:
    """Return a numpy-callable for ``fn``.

    ``n_outputs=None`` means a scalar function; an int means a vector field with
    that many components (SymPy input then must be a Matrix or list of expressions).
    """
    if n_outputs is None:
        if _is_sympy_vector(fn):
            raise ValueError("got a sequence of expressions; use visualize_vf() for vector fields")
        if _is_sympy(fn):
            syms = _resolve_symbols([fn], n_inputs, symbols)
            return sp.lambdify(syms, fn, modules="numpy")
    else:
        if _is_sympy_vector(fn):
            comps = list(fn)
            if len(comps) != n_outputs:
                raise ValueError(
                    f"vector field has {len(comps)} component(s) but dim={n_outputs} needs "
                    f"{n_outputs}"
                )
            syms = _resolve_symbols(comps, n_inputs, symbols)
            funcs = [sp.lambdify(syms, sp.sympify(c), modules="numpy") for c in comps]
            return lambda *args: [f(*args) for f in funcs]
        if _is_sympy(fn):
            raise ValueError(
                "a single SymPy expression is a scalar field; pass a Matrix or list of "
                f"{n_outputs} expressions"
            )

    if not callable(fn):
        raise ValueError(f"fn must be a callable or a SymPy expression, got {type(fn).__name__}")
    if symbols is not None:
        raise ValueError("symbols= only applies to SymPy expressions")
    _check_arity(fn, n_inputs)
    return fn


def _real_array(value, shape: tuple[int, ...], what: str) -> np.ndarray:
    arr = np.asarray(value)
    if np.iscomplexobj(arr):
        raise ValueError(f"{what} returned complex values; only real-valued output can be plotted")
    try:
        arr = np.broadcast_to(arr.astype(float), shape)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{what} returned shape {np.shape(value)}, not broadcastable to input shape {shape}"
        ) from exc
    return np.array(arr)  # writable copy


def evaluate(
    func: Callable, inputs: Sequence[np.ndarray], *, n_outputs: Optional[int] = None
) -> list[np.ma.MaskedArray]:
    """Evaluate ``func`` on ``inputs`` and mask non-finite output.

    Returns one masked array per output component (one for scalar functions).
    The mask is shared across components: a point is dropped if any component
    there is non-finite.
    """
    shape = np.shape(inputs[0])
    with np.errstate(all="ignore"):
        raw = func(*inputs)

    if n_outputs is None:
        comps = [_real_array(raw, shape, "function")]
    else:
        # An ndarray stacks its components along the first axis: either one
        # full-shape array per component (ndim > grid ndim), or a 1-D array of
        # constant components, accepted exactly like the tuple ``(u, v)``.
        if isinstance(raw, np.ndarray) and (raw.ndim > len(shape) or raw.ndim == 1):
            raw = list(raw)
        if not isinstance(raw, (list, tuple)) or len(raw) != n_outputs:
            got = len(raw) if isinstance(raw, (list, tuple)) else type(raw).__name__
            raise ValueError(f"vector field must return {n_outputs} components, got {got}")
        comps = [_real_array(c, shape, "vector field") for c in raw]

    bad = np.zeros(shape, dtype=bool)
    for c in comps:
        bad |= ~np.isfinite(c)
    n_bad = int(bad.sum())
    if n_bad == bad.size:
        raise ValueError(
            "function produced no finite values on the sampled domain; nothing to plot"
        )
    if n_bad:
        warnings.warn(
            f"{n_bad} of {bad.size} sampled points are non-finite (nan/inf) and were "
            "left out of the plot",
            RuntimeWarning,
            stacklevel=3,
        )
    return [np.ma.masked_array(c, mask=bad.copy()) for c in comps]
