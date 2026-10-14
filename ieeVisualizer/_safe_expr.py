"""Bounded construction of SymPy expressions from untrusted text.

SymPy evaluates while it builds an expression, with exact arithmetic that
cannot be interrupted: ``9**9**9`` asks for an integer with ~370 million
digits, ``gamma(10**6)`` for an exact factorial, ``exp(10**9*log(9))`` is
rewritten to ``9**(10**9)``, and a float literal such as ``1e999999999``
becomes a huge exact integer as soon as it is compared with one. A short
expression string can therefore hang a process. A character or name
allow-list does not help, and neither does inspecting the *evaluated* tree:
by then the work has been done.

Callers therefore parse with :func:`parse_unevaluated` (nothing is
evaluated, not even function calls) and then call :func:`build_bounded`,
which rebuilds the tree bottom-up with evaluation switched back on. Each
node is inspected after its arguments have been evaluated (so
``x - x + 999999999`` is seen as the constant it becomes, and caller
constants are already substituted) and *before* the node itself is
evaluated. The limits:

* at most :data:`MAX_LENGTH` characters, :data:`MAX_NODES` nodes and :data:`MAX_DEPTH` levels;
* no numeric literal (checked in the text, before parsing) and no number
  produced on the way whose magnitude (or reciprocal) exceeds ``10**MAX_DIGITS`` --
  float64 overflows at ~1.8e308, so nothing larger can be drawn anyway;
* a power with a numeric exponent: ``|exponent| <= MAX_EXPONENT`` and the
  exact result of any number in the base must stay within ``MAX_DIGITS``
  digits; a symbolic exponent of a base that contains numbers (or of ``E``)
  may not contain a number larger than ``MAX_EXPONENT`` (SymPy combines
  ``7**x * 7**(1000000000 - x)`` into ``7**1000000000``);
* ``exp(c*log(b))`` (which SymPy rewrites to ``b**c``, also per additive
  term and after combining logarithms) is held to the same power limits;
* ``gamma``/``factorial`` and similar of a number larger than
  :data:`MAX_FACTORIAL_ARG` in magnitude.

Anything outside the limits raises :class:`UnsafeExpression` quickly; the
caller turns it into its own structured error.

This module is deliberately self-contained (SymPy imported lazily) so the
render and template packages can share the same rules.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_LENGTH = 1000
MAX_NODES = 500
MAX_DEPTH = 100
MAX_EXPONENT = 1000
MAX_DIGITS = 330  # float64 spans ~1e-324 (subnormal) .. ~1.8e308
MAX_FACTORIAL_ARG = 1000

_COMBINATORIAL = ("gamma", "factorial", "factorial2", "subfactorial", "RisingFactorial",
                  "FallingFactorial", "binomial", "loggamma", "fibonacci", "lucas",
                  "bernoulli", "harmonic", "catalan", "bell", "polygamma")


_LITERAL = re.compile(r"(?<![A-Za-z_0-9.])(\d+\.?\d*|\.\d+)(?:[eE]([+-]?\d+))?")


class UnsafeExpression(ValueError):
    """The expression would need unbounded exact arithmetic (or is too big)."""


def check_text(text: str) -> None:
    """Refuse text longer than :data:`MAX_LENGTH` and numeric literals SymPy
    would expand exactly while parsing (``1e999999999`` is converted digit
    by digit)."""
    if len(text) > MAX_LENGTH:
        raise UnsafeExpression(f"the expression is longer than {MAX_LENGTH} characters.")
    for m in _LITERAL.finditer(text):
        digits, exponent = m.group(1), m.group(2)
        if len(digits.replace(".", "").lstrip("0")) > MAX_DIGITS or (
                exponent is not None and len(exponent.lstrip("+-0")) > 6) or (
                exponent is not None and abs(int(exponent)) > MAX_DIGITS):
            raise UnsafeExpression(
                f"the number {_short(m.group(0))} is outside +-1e{MAX_DIGITS} (float64 cannot "
                "represent it).")


def parse_unevaluated(text: str, local_dict: dict[str, Any]):
    """Parse ``text`` with SymPy's standard transformations without
    evaluating anything. ``text`` must already have passed the caller's
    character/name allow-list: SymPy's parser runs Python ``eval``."""
    import sympy
    from sympy.parsing.sympy_parser import parse_expr, standard_transformations

    check_text(text)
    with sympy.evaluate(False):
        return parse_expr(text, local_dict=local_dict,
                          transformations=standard_transformations, evaluate=False)


def build_bounded(expr, substitutions: dict | None = None):
    """Evaluate the unevaluated ``expr`` bottom-up within the module limits,
    replacing atoms per ``substitutions`` first. Raises
    :class:`UnsafeExpression`."""
    import sympy

    if not isinstance(expr, sympy.Expr):
        raise TypeError(f"expected a single SymPy expression, got {type(expr).__name__}")
    _check_size(expr)
    subs = substitutions or {}
    for value in subs.values():
        _check_numbers(value, "a substituted value")
    combinatorial = tuple(c for c in (getattr(sympy, n, None) for n in _COMBINATORIAL)
                          if isinstance(c, type))
    return _build(expr, subs, sympy, combinatorial)


def _check_size(expr) -> None:
    nodes = 0
    stack = [(expr, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise UnsafeExpression(f"the expression has more than {MAX_NODES} terms.")
        if depth > MAX_DEPTH:
            raise UnsafeExpression(f"the expression is nested more than {MAX_DEPTH} levels deep.")
        stack.extend((a, depth + 1) for a in getattr(node, "args", ()))


def _build(node, subs, sympy, combinatorial):
    if not node.args:
        out = subs.get(node, node)
        _check_numbers(out, "the number")
        return out
    args = [_build(a, subs, sympy, combinatorial) for a in node.args]
    if isinstance(node, sympy.Pow):
        _check_pow(args[0], args[1], sympy)
    elif isinstance(node, sympy.exp):
        _check_exp(args[0], sympy)
    elif isinstance(node, combinatorial):
        for a in args:
            if a.is_Number and _exceeds(a, MAX_FACTORIAL_ARG):
                raise UnsafeExpression(
                    f"{node.func.__name__} of {_short(a)} would need exact arithmetic on a huge "
                    f"number; its argument must be within +-{MAX_FACTORIAL_ARG}.")
    out = node.func(*args)
    _check_numbers(out, "an intermediate result")
    return out


def _digits(n) -> float:
    """Decimal order of magnitude of a finite number (of it or its
    reciprocal, whichever is larger)."""
    if n.is_Rational:
        p, q = abs(int(n.p)), int(n.q)
        return max(math.log10(p) if p else 0.0, math.log10(q))
    if n.is_Float:
        _sign, man, exp, bc = n._mpf_
        if not man:
            return 0.0
        return abs(exp + bc) * math.log10(2)
    return 0.0


def _numbers(expr):
    return [a for a in expr.atoms() if getattr(a, "is_Number", False) and a.is_finite]


def _check_numbers(expr, what: str) -> None:
    for n in _numbers(expr):
        if _digits(n) > MAX_DIGITS:
            raise UnsafeExpression(
                f"{what} {_short(n)} has more than {MAX_DIGITS} digits (float64 cannot "
                "represent it).")


def _exceeds(n, limit) -> bool:
    try:
        return bool(abs(n) > limit)
    except TypeError:  # nan
        return False


def _check_growth(base, exponent) -> None:
    """``exponent`` is a finite Number; refuse an exact power that would
    explode."""
    if _exceeds(exponent, MAX_EXPONENT):
        raise UnsafeExpression(
            f"exponent {_short(exponent)} is larger than {MAX_EXPONENT} in magnitude.")
    e = abs(float(exponent))
    # Summed, not max: SymPy may multiply the base's numbers together first
    # (``exp(c*(log(9) + log(7)))`` becomes ``63**c``).
    worst = sum(_digits(n) for n in _numbers(base))
    if worst * e > MAX_DIGITS:
        raise UnsafeExpression(
            f"{_short(base)} to the power {_short(exponent)} would have about "
            f"{int(worst * e)} digits (limit {MAX_DIGITS}).")


def _check_pow(base, exponent, sympy) -> None:
    if exponent.is_Number:
        if exponent.is_finite:
            _check_growth(base, exponent)
        return
    if base is sympy.E:
        _check_exp(exponent, sympy)
    if base is sympy.E or _numbers(base):
        for n in _numbers(exponent):
            if _exceeds(n, MAX_EXPONENT):
                raise UnsafeExpression(
                    f"the exponent of {_short(base)} contains {_short(n)}, larger than "
                    f"{MAX_EXPONENT} in magnitude.")


def _check_exp(arg, sympy) -> None:
    """SymPy rewrites ``exp(c*log(b))`` to ``b**c`` (per additive term)."""
    if not arg.has(sympy.log):
        return
    for term in sympy.Add.make_args(arg):
        if not term.has(sympy.log):
            continue
        coeff, rest = term.as_coeff_Mul()
        if coeff.is_finite:
            _check_growth(rest, coeff)


def _short(x) -> str:
    try:
        s = str(x)
    except ValueError:  # an integer too long for str()
        return "a very large number"
    return s if len(s) <= 40 else s[:37] + "..."
