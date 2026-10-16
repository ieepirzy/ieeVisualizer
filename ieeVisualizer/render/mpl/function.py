"""Sample a symbolic ``function`` mark.

The expression string comes from a spec, which may come from an agent, so it
is checked before SymPy sees it: SymPy's parser evaluates Python, and a spec
must not be able to run code. Only arithmetic, numbers, the mark's
``variables`` and ``parameters``, and an allow-list of mathematical functions
and constants are accepted; anything else is a :class:`RenderError` naming
the offending identifier. SymPy evaluates exactly while it builds, so the
text is parsed unevaluated and rebuilt under the limits of
:mod:`ieeVisualizer._safe_expr` (powers, factorial-like functions, number
size, expression size); ``9^9^9`` is an ``expression_too_large`` error
instead of a hang. A sub-expression that evaluates to an undefined or
infinite constant (``1/0``, ``tan(pi/2)``, ``log(0)``) is an
``invalid_expression`` error.

Sampling reuses the legacy :func:`ieeVisualizer._evaluate.evaluate` rule:
non-finite outputs are masked (gaps, reported as a warning) and an
expression with no finite value on its domain is an error.

Inside :func:`expression_cache` (an animation render enters it), each
expression text is parsed once and each (text, parameter values) pair is
built and compiled once, however many states or passes draw it; the result
is the same as without the cache.
"""

from __future__ import annotations

import contextlib
import re
import warnings as _warnings
from contextvars import ContextVar

import numpy as np

from ..._safe_expr import (
    UndefinedExpression,
    UnsafeExpression,
    build_bounded,
    parse_unevaluated,
)
from ...spec import FunctionMark
from ..base import RenderError, RenderWarning, issue

ALLOWED_FUNCTIONS = frozenset({
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
    "asinh", "acosh", "atanh", "exp", "log", "sqrt", "Abs", "abs", "sign", "floor",
    "ceiling", "Min", "Max", "erf", "gamma", "sec", "csc", "cot",
})
ALLOWED_CONSTANTS = frozenset({"pi", "E"})
_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9_+\-*/^().,\s]*$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Numeric literals (incl. exponents such as 1e-3) are removed before looking
# for identifiers, so the "e" of an exponent is not mistaken for a symbol.
_NUMBER = re.compile(r"(?<![A-Za-z_0-9])(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
# A '.' is only allowed inside a number (no attribute access).
_BAD_DOT = re.compile(r"(?<![0-9])\.(?![0-9])|[A-Za-z_]\.|\.[A-Za-z_]")
_CACHE: ContextVar[dict | None] = ContextVar("ieev_expression_cache", default=None)


@contextlib.contextmanager
def expression_cache():
    """Parse and compile each expression once within the block (nested
    blocks share the outermost cache; a new thread needs ``copy_context``)."""
    if _CACHE.get() is not None:
        yield
        return
    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


def _cached(key: tuple, make):
    """``make()`` once per ``key`` in the current cache (errors included)."""
    cache = _CACHE.get()
    if cache is None:
        return make()
    if key not in cache:
        try:
            cache[key] = (True, make())
        except RenderError as e:
            cache[key] = (False, e)
    ok, value = cache[key]
    if not ok:
        raise value
    return value


def _key(mark: FunctionMark) -> tuple:
    return (mark.expression, tuple(mark.variables), tuple(sorted(mark.parameters.items())))


def parse(mark: FunctionMark, path: str):
    """Return ``(sympy_expr, symbols)`` for ``mark`` with parameters
    substituted."""
    return _cached(("built", path, *_key(mark)), lambda: _parse(mark, path))


def _parse(mark: FunctionMark, path: str):
    import sympy as sp

    expr_s = mark.expression or ""
    p = path + "/expression"
    if not _ALLOWED_CHARS.match(expr_s) or "__" in expr_s or _BAD_DOT.search(expr_s):
        raise RenderError([issue(p, "invalid_expression",
                                 f"expression {expr_s!r} may only contain numbers, variables, "
                                 "parameters, + - * / ^ ( ) and the functions "
                                 f"{', '.join(sorted(ALLOWED_FUNCTIONS))}.")])
    names = set(mark.variables) | set(mark.parameters)
    for ident in _IDENT.findall(_NUMBER.sub(" ", expr_s)):
        if ident not in names and ident not in ALLOWED_FUNCTIONS \
                and ident not in ALLOWED_CONSTANTS:
            raise RenderError([issue(p, "unknown_symbol",
                                     f"expression uses {ident!r}, which is neither a variable "
                                     f"({', '.join(mark.variables)}), a parameter "
                                     f"({', '.join(sorted(mark.parameters)) or 'none'}) nor an "
                                     "allowed function; add it to /parameters or fix the "
                                     "expression.")])
    symbols = {n: sp.Symbol(n, real=True) for n in names}
    local = dict(symbols)
    for f in ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS:
        if f not in local:
            local[f] = getattr(sp, "Abs" if f == "abs" else f)
    values = {symbols[k]: sp.sympify(v) for k, v in mark.parameters.items()}
    try:
        text = expr_s.replace("^", "**")
        tree = _cached(("tree", text, frozenset(names)), lambda: parse_unevaluated(text, local))
        if not isinstance(tree, sp.Expr):
            raise TypeError(f"it is a {type(tree).__name__}, not a single expression")
        # Parameters are substituted while the tree is rebuilt, so every power
        # is checked with its final numbers before SymPy evaluates it.
        expr = build_bounded(tree, values)
    except UnsafeExpression as e:
        raise RenderError([issue(p, "expression_too_large",
                                 f"expression {expr_s!r} cannot be evaluated safely: {e} "
                                 "Simplify it or use smaller constants.")]) from None
    except UndefinedExpression as e:
        raise RenderError([issue(p, "invalid_expression",
                                 f"expression {expr_s!r} cannot be drawn: {e}")]) from None
    except Exception as e:  # untrusted input: any SymPy failure is a parse error
        raise RenderError([issue(p, "invalid_expression",
                                 f"expression {expr_s!r} could not be parsed: {e}")]) from None
    var_syms = [symbols[v] for v in mark.variables]
    free = expr.free_symbols - set(var_syms)
    if free:  # pragma: no cover - guarded by the identifier check above
        raise RenderError([issue(p, "unknown_symbol", f"unbound symbols {sorted(map(str, free))}")])
    return expr, var_syms


def sample(mark: FunctionMark, path: str, log_axes: tuple[bool, ...],
           warnings: list[RenderWarning]) -> tuple[list[np.ndarray], np.ma.MaskedArray]:
    """Evaluate on the domain grid; return ``(inputs, masked_output)``.

    One variable -> 1D arrays of ``samples`` points; two -> a
    ``samples x samples`` grid. Variables on a log-scaled axis are sampled
    geometrically.
    """
    import sympy as sp

    from ..._evaluate import evaluate

    expr, syms = parse(mark, path)
    axes_1d = []
    for var, is_log in zip(mark.variables, log_axes):
        lo, hi = mark.domain[var]
        axes_1d.append(np.geomspace(lo, hi, mark.samples) if is_log
                       else np.linspace(lo, hi, mark.samples))
    inputs = list(np.meshgrid(*axes_1d)) if len(axes_1d) == 2 else axes_1d
    def compile_():
        try:
            return sp.lambdify(syms, expr, modules="numpy")
        except Exception as e:  # noqa: BLE001 - untrusted input: a printer failure is reported
            raise RenderError([issue(path + "/expression", "invalid_expression",
                                     f"expression {mark.expression!r} cannot be converted to "
                                     f"a numeric function: {type(e).__name__}: {e}")]) from None

    fn = _cached(("fn", path, *_key(mark)), compile_)
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        try:
            (out,) = evaluate(fn, inputs)
        except (ValueError, TypeError, ArithmeticError) as e:
            # TypeError/OverflowError: NumPy cannot take e.g. exp() of an
            # integer beyond float64 (exp(10^300)).
            code = "empty_series" if "no finite values" in str(e) else "function_not_drawable"
            raise RenderError([issue(path, code,
                                     f"the expression could not be drawn: {e}.")]) from None
    for w in caught:
        if issubclass(w.category, RuntimeWarning):
            warnings.append(RenderWarning("nonfinite_samples_omitted", str(w.message), path))
    return inputs, out
