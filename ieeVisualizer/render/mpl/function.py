"""Sample a symbolic ``function`` mark.

The expression string comes from a spec, which may come from an agent, so it
is checked before SymPy sees it: SymPy's parser evaluates Python, and a spec
must not be able to run code. Only arithmetic, numbers, the mark's
``variables`` and ``parameters``, and an allow-list of mathematical functions
and constants are accepted; anything else is a :class:`RenderError` naming
the offending identifier.

Sampling reuses the legacy :func:`ieeVisualizer._evaluate.evaluate` rule:
non-finite outputs are masked (gaps, reported as a warning) and an
expression with no finite value on its domain is an error.
"""

from __future__ import annotations

import re
import warnings as _warnings

import numpy as np

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


def parse(mark: FunctionMark, path: str):
    """Return ``(sympy_expr, symbols)`` for ``mark`` with parameters
    substituted."""
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
    try:
        expr = sp.sympify(expr_s.replace("^", "**"), locals=local, convert_xor=True)
    except (sp.SympifyError, SyntaxError, TypeError, ValueError) as e:
        raise RenderError([issue(p, "invalid_expression",
                                 f"expression {expr_s!r} could not be parsed: {e}")]) from None
    expr = expr.subs({symbols[k]: v for k, v in mark.parameters.items()})
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
    fn = sp.lambdify(syms, expr, modules="numpy")
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        try:
            (out,) = evaluate(fn, inputs)
        except ValueError as e:
            code = "empty_series" if "no finite values" in str(e) else "function_not_drawable"
            raise RenderError([issue(path, code,
                                     f"the expression could not be drawn: {e}.")]) from None
    for w in caught:
        if issubclass(w.category, RuntimeWarning):
            warnings.append(RenderWarning("nonfinite_samples_omitted", str(w.message), path))
    return inputs, out
