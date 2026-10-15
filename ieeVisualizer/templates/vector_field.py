"""Built-in ``vector_field_2d`` template: a 2D vector field drawn as a quiver.

Two input modes, exactly one per build:

* **Expression mode** (no data): ``u`` and ``v`` are expressions in ``x`` and
  ``y`` (e.g. ``u="-y", v="x"``), sampled on a regular ``grid`` over
  ``x_range`` x ``y_range``. Spec schema 1.0 has no symbolic quiver (a
  ``function`` mark is a scalar curve), so the template samples the field
  itself: it parses the expressions with SymPy, evaluates them with
  ``lambdify`` + NumPy at the grid points, and stores the samples as an
  inline snapshot. SymPy and NumPy are imported lazily inside the build, so
  ``import ieeVisualizer.templates`` stays light. The expressions, constants,
  grid and ranges are recorded in the dataset provenance and in
  ``metadata["template"]`` so the samples can be reproduced. Grid order is
  row-major: ``y`` outer, ``x`` inner (``numpy.meshgrid`` ``indexing="xy"``,
  raveled). The layer role defaults to ``reference``: an evaluated formula is
  not a measurement. A sample where an expression is nan/inf (e.g. ``1/x`` at
  ``x = 0``) is stored as null and reported as a ``nonfinite_samples``
  warning, never replaced by a number. Without units the field is treated as
  unitless mathematics (no warning).
* **Sampled mode**: ``data`` with columns for x, y, u, v (names via
  ``x_column`` ... ``v_column``). Units come from the columns; ``x_unit`` /
  ``y_unit`` / ``vector_unit`` may only fill in a missing unit, never
  contradict one. ``u`` and ``v`` must share a unit. Null positions are an
  error (an arrow cannot be placed); null components stay null. Role defaults
  to ``observed``.

Expressions are untrusted input. Before SymPy sees one, it is parsed with
Python's ``ast`` and only numbers, the names ``x``, ``y``, caller constants,
``pi``, ``E`` and a fixed list of functions, combined with ``+ - * / **`` and
unary minus, are accepted (no attributes, subscripts, keywords, strings or
lambdas). A power whose base and exponent are both literal constants is
refused there. That alone cannot stop SymPy's exact arithmetic
(``(0*x+9)**999999999`` simplifies to a constant power, and so does
``k**(x-x+999999999)`` once ``k`` is substituted), so the expression is then
parsed unevaluated and rebuilt under the limits of
:mod:`ieeVisualizer._safe_expr`: every power, ``exp`` of a logarithm and
factorial-like call is checked after constants are substituted and before
SymPy evaluates it (exponents at most 1000 in magnitude, no number beyond
about 10^+-330, bounded size). A violation is an ``expression_too_large``
issue from ``check()`` and a :class:`TemplateInputError` from ``build()``,
raised immediately.
"""

from __future__ import annotations

import ast
import keyword
from typing import Any

from .._safe_expr import UnsafeExpression, build_bounded, parse_unevaluated
from ..spec import Axis, Column, DataSnapshot, FigureSpec, Provenance, QuiverMark
from ..spec._common import suggest
from .base import (
    ColumnRequirement,
    ComponentTemplate,
    Issues,
    Parameter,
    column_path,
    param_path,
)

VARIABLES = ("x", "y")
FUNCTIONS = ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
             "exp", "log", "sqrt", "abs", "sign", "floor", "ceiling", "Min", "Max")
CONSTANTS = ("pi", "E")
MAX_EXPRESSION_LENGTH = 500
GRID_LIMITS = (2, 200)

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_UNARY = (ast.UAdd, ast.USub)


def _has_name(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) for n in ast.walk(node))


def check_expression(text: str, constants: dict[str, float]) -> tuple[str, str] | None:
    """Return ``None`` if ``text`` is an acceptable expression, else
    ``(code, message)``."""
    if len(text) > MAX_EXPRESSION_LENGTH:
        return ("invalid_expression",
                f"expression is longer than {MAX_EXPRESSION_LENGTH} characters.")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        hint = " Use ** for powers, not ^." if "^" in text else ""
        return ("invalid_expression", f"cannot parse {text!r}: {e.msg}.{hint}")
    names = set(VARIABLES) | set(constants) | set(CONSTANTS)
    callees = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)) or isinstance(node, _BINOPS + _UNARY):
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BINOPS):
                hint = " Use ** for powers, not ^." if isinstance(node.op, ast.BitXor) else ""
                return ("invalid_expression",
                        f"operator {type(node.op).__name__} is not allowed in {text!r}; use "
                        f"+ - * / **.{hint}")
            if isinstance(node.op, ast.Pow) and not (_has_name(node.left)
                                                      or _has_name(node.right)):
                return ("invalid_expression",
                        f"constant power in {text!r}; write the number directly.")
            continue
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY):
                return ("invalid_expression", f"operator not allowed in {text!r}.")
            continue
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return ("invalid_expression", f"only numbers may appear as literals in {text!r}.")
            continue
        if isinstance(node, ast.Call):
            fn = node.func
            if not isinstance(fn, ast.Name) or fn.id not in FUNCTIONS:
                fname = fn.id if isinstance(fn, ast.Name) else "?"
                return ("unknown_symbol",
                        f"function {fname!r} is not allowed.{suggest(fname, FUNCTIONS)} "
                        f"Allowed: {', '.join(FUNCTIONS)}.")
            if node.keywords:
                return ("invalid_expression", "keyword arguments are not allowed.")
            continue
        if isinstance(node, ast.Name):
            if id(node) in callees:
                continue  # checked with its Call above
            if node.id in FUNCTIONS:
                return ("invalid_expression",
                        f"function {node.id!r} must be called with arguments, e.g. "
                        f"{node.id}(x).")
            if node.id not in names:
                return ("unknown_symbol",
                        f"unknown name {node.id!r} in {text!r}.{suggest(node.id, names)} "
                        f"Use x, y, pi, E, a function ({', '.join(FUNCTIONS[:6])}, ...) or "
                        "pass it in 'constants'.")
            continue
        return ("invalid_expression",
                f"{type(node).__name__} is not allowed in an expression ({text!r}).")
    return None


class VectorField2DTemplate(ComponentTemplate):
    name = "vector_field_2d"
    version = "1.0.0"
    title = "2D vector field"
    description = (
        "Quiver plot of a 2D vector field, either sampled from expressions u(x, y), v(x, y) on "
        "a regular grid (expressions recorded for provenance) or from caller-supplied x, y, "
        "u, v columns."
    )
    data_required = False
    parameters = (
        Parameter("u", "string", "Expression for the x component in x and y, e.g. '-y'. "
                  "Expression mode; give with v and no data."),
        Parameter("v", "string", "Expression for the y component, e.g. 'x'."),
        Parameter("constants", "number_map", "Named numeric constants used in u/v, e.g. "
                  "{'k': 0.5}."),
        Parameter("x_range", "range", "Expression mode: [min, max] of x.", default=[-2.0, 2.0]),
        Parameter("y_range", "range", "Expression mode: [min, max] of y.", default=[-2.0, 2.0]),
        Parameter("grid", "grid", f"Expression mode: points per axis, n or [nx, ny], each "
                  f"{GRID_LIMITS[0]}..{GRID_LIMITS[1]}.", default=20),
        Parameter("x_column", "column", "Sampled mode: x position column.", default="x"),
        Parameter("y_column", "column", "Sampled mode: y position column.", default="y"),
        Parameter("u_column", "column", "Sampled mode: x component column.", default="u"),
        Parameter("v_column", "column", "Sampled mode: y component column.", default="v"),
        Parameter("x_unit", "string", "Unit of x; in sampled mode only if the column has none."),
        Parameter("y_unit", "string", "Unit of y; in sampled mode only if the column has none."),
        Parameter("vector_unit", "string", "Unit of u and v; in sampled mode only if the "
                  "columns have none."),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption."),
        Parameter("x_label", "string", "X-axis label (default: column description or name)."),
        Parameter("y_label", "string", "Y-axis label (default: column description or name)."),
        Parameter("normalize", "boolean", "Draw unit-length arrows (direction only).",
                  default=False),
        Parameter("scale", "number", "Arrow scale hint for the renderer (> 0)."),
        Parameter("role", "string", "Layer role; default 'reference' for expressions, "
                  "'observed' for data.", allowed=("observed", "reference")),
    )
    columns = (
        ColumnRequirement("x position", "x_column", ("float", "int"), "arrow tail x"),
        ColumnRequirement("y position", "y_column", ("float", "int"), "arrow tail y"),
        ColumnRequirement("x component", "u_column", ("float", "int"),
                          "vector x component; same unit as v"),
        ColumnRequirement("y component", "v_column", ("float", "int"),
                          "vector y component; same unit as u"),
    )
    notes = (
        "Produces one quiver layer over dataset 'field' (columns x, y, u, v).",
        "Expression mode samples u, v with SymPy/NumPy on a grid (y outer, x inner) and records "
        "the expressions; non-finite samples become null with a warning.",
        "Columns are used only in sampled mode (when data is given).",
    )

    # ----------------------------------------------------------------- checks
    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        has_expr = p["u"] is not None or p["v"] is not None
        if data is not None and has_expr:
            issues.error(param_path("u" if p["u"] is not None else "v"),
                         "conflicting_parameters",
                         "give either expressions u and v (no data) or data with x, y, u, v "
                         "columns, not both.")
            return
        if data is None and not has_expr:
            issues.error("/data", "missing_data",
                         "give expressions u and v in x and y (e.g. u='-y', v='x'), or data "
                         "with x, y, u, v columns.")
            return
        if p["scale"] is not None and not p["scale"] > 0:
            # build() would produce a quiver the spec validator refuses
            # (scale must be > 0); check() reports it as the input problem.
            issues.error(param_path("scale"), "invalid_parameter",
                         f"scale must be > 0 (or omitted for auto), got {p['scale']!r}.")
        if data is None:
            self._check_expressions(p, issues)
        else:
            self._check_sampled(data, p, issues)

    def _check_expressions(self, p: dict[str, Any], issues: Issues) -> None:
        constants = p["constants"] or {}
        for k in constants:
            if (not k.isidentifier() or keyword.iskeyword(k) or k in VARIABLES
                    or k in FUNCTIONS or k in CONSTANTS):
                issues.error(param_path("constants"), "invalid_parameter",
                             f"constant name {k!r} must be an identifier other than x, y, a "
                             "function name, pi or E.")
        for key in ("u", "v"):
            if p[key] is None:
                issues.error(param_path(key), "missing_parameter",
                             f"expression mode needs both u and v; {key!r} is missing.")
                continue
            problem = check_expression(p[key], constants)
            if problem:
                issues.error(param_path(key), problem[0], problem[1])
        lo, hi = GRID_LIMITS
        if any(not lo <= n <= hi for n in p["grid"]):
            issues.error(param_path("grid"), "invalid_parameter",
                         f"grid must be between {lo} and {hi} points per axis, got {p['grid']}.")
        if not issues.errors:
            # Dry-run the sampling so check() reports everything build() would refuse
            # (SymPy arity errors, a field that is non-finite everywhere). Its warnings
            # are dropped here; build() samples again and records them once.
            trial = Issues()
            self._sample(p, trial)
            issues.items.extend(trial.errors)

    def _check_sampled(self, data: DataSnapshot, p: dict[str, Any], issues: Issues) -> None:
        names = [p[k] for k in ("x_column", "y_column", "u_column", "v_column")]
        if len(set(names)) != 4:
            issues.error(param_path("x_column"), "conflicting_parameters",
                         f"x, y, u and v must be four different columns, got {names}.")
            return
        for key in ("x_column", "y_column"):
            nulls = sum(v is None for v in data.columns[p[key]].values)
            if nulls:
                issues.error(column_path(p[key]), "null_in_key_column",
                             f"position column {p[key]!r} has {nulls} null value(s); those "
                             "arrows cannot be placed. Drop the rows upstream.")
        cu, cv = data.columns[p["u_column"]].unit, data.columns[p["v_column"]].unit
        if cu != cv:
            issues.error(column_path(p["v_column"]), "mixed_units",
                         f"u is in {cu!r} but v is in {cv!r}; both components need the same "
                         "unit (declare it, or convert upstream).")
        for override, cols in (("x_unit", ["x_column"]), ("y_unit", ["y_column"]),
                               ("vector_unit", ["u_column", "v_column"])):
            declared = data.columns[p[cols[0]]].unit
            given = p[override]
            if declared is not None and given is not None and given != declared:
                issues.error(param_path(override), "unit_override_conflict",
                             f"{override} {given!r} contradicts the declared unit "
                             f"{declared!r} of column {p[cols[0]]!r}; nothing is converted.")
            elif declared is None and given is None:
                issues.warn(column_path(p[cols[0]]), "missing_unit",
                            f"column {p[cols[0]]!r} has no unit; set it or pass {override}.")

    # ------------------------------------------------------------------ build
    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        meta: dict[str, Any] = {}
        if data is None:
            field, meta = self._sample(p, issues)
            names = dict(zip("xyuv", "xyuv"))
            role = p["role"] or "reference"
        else:
            names = {k: p[f"{k}_column"] for k in "xyuv"}
            units = {"x": p["x_unit"], "y": p["y_unit"], "u": p["vector_unit"],
                     "v": p["vector_unit"]}
            cols = {}
            for k, name in names.items():
                c = data.columns[name]
                cols[name] = Column(values=list(c.values), dtype=c.dtype,
                                    unit=c.unit if c.unit is not None else units[k],
                                    description=c.description, denominator=c.denominator,
                                    aggregation=c.aggregation)
            field = DataSnapshot(columns=cols, provenance=data.provenance)
            meta["input_hash"] = data.content_hash()
            role = p["role"] or "observed"
        if issues.errors:
            return FigureSpec()
        xc, yc = field.columns[names["x"]], field.columns[names["y"]]
        spec = FigureSpec(
            title=p["title"], caption=p["caption"],
            datasets={"field": field},
            layers=[QuiverMark(data="field", encoding=names, role=role,
                               normalize=p["normalize"], scale=p["scale"])],
            axes={
                "x": Axis(label=p["x_label"] or xc.description or names["x"], unit=xc.unit),
                "y": Axis(label=p["y_label"] or yc.description or names["y"], unit=yc.unit),
            },
        )
        spec.metadata["template"] = meta
        return spec

    def _sample(self, p: dict[str, Any], issues: Issues) -> tuple[DataSnapshot, dict[str, Any]]:
        import numpy as np
        import sympy

        constants = dict(p["constants"] or {})
        ns: dict[str, Any] = {n: sympy.Symbol(n, real=True) for n in VARIABLES}
        ns.update({n: sympy.Symbol(n) for n in constants})
        ns.update({"pi": sympy.pi, "E": sympy.E, "abs": sympy.Abs, "ceiling": sympy.ceiling,
                   "Min": sympy.Min, "Max": sympy.Max})
        for f in FUNCTIONS:
            if f not in ns:
                ns[f] = getattr(sympy, f)
        values = {ns[k]: sympy.Float(c) if isinstance(c, float) else sympy.Integer(c)
                  for k, c in constants.items()}
        exprs = {}
        for key in ("u", "v"):
            try:
                # Parsing is safe: check_expression admitted only whitelisted names and
                # operators. Evaluation is bounded: SymPy computes exactly while it
                # builds, so the unevaluated tree is rebuilt with every power checked
                # after constants are substituted and before it is evaluated.
                tree = parse_unevaluated(p[key], ns)
                if not isinstance(tree, sympy.Expr):
                    raise TypeError(f"it is a {type(tree).__name__}, not one expression")
                e = build_bounded(tree, values)
            except UnsafeExpression as err:
                issues.error(param_path(key), "expression_too_large",
                             f"{key} = {p[key]!r} cannot be evaluated safely: {err} Simplify "
                             "it or use smaller constants.")
                continue
            except Exception as err:  # untrusted input: any SymPy failure is reported
                issues.error(param_path(key), "invalid_expression",
                             f"SymPy cannot interpret {p[key]!r}: {err}")
                continue
            if not isinstance(e, sympy.Expr) or e.free_symbols - {ns["x"], ns["y"]}:
                issues.error(param_path(key), "invalid_expression",
                             f"{p[key]!r} does not reduce to a numeric expression in x and y.")
                continue
            exprs[key] = e
        if issues.errors:
            return DataSnapshot(), {}

        nx, ny = p["grid"]
        xs = np.linspace(p["x_range"][0], p["x_range"][1], nx)
        ys = np.linspace(p["y_range"][0], p["y_range"][1], ny)
        gx, gy = np.meshgrid(xs, ys, indexing="xy")
        values = {}
        for key, e in exprs.items():
            fn = sympy.lambdify((ns["x"], ns["y"]), e, modules="numpy")
            with np.errstate(all="ignore"):
                raw = np.broadcast_to(np.asarray(fn(gx, gy)), gx.shape)
            if np.iscomplexobj(raw):  # e.g. a fractional power of a negative number
                raw = np.where(np.imag(raw) == 0, np.real(raw), np.nan)
            arr = raw.astype(float).ravel()
            bad = ~np.isfinite(arr)
            if bad.all():
                issues.error(param_path(key), "all_null_column",
                             f"{key} = {p[key]!r} is not finite at any grid point; nothing "
                             "would be drawn. Check the expression and ranges.")
            elif bad.any():
                issues.warn(param_path(key), "nonfinite_samples",
                            f"{key} = {p[key]!r} is nan/inf at {int(bad.sum())} of {arr.size} "
                            "grid points; those samples are stored as null (no arrow), not as "
                            "a number.")
            values[key] = np.where(bad, np.nan, arr).tolist()  # nan -> null in Column

        vu = p["vector_unit"]
        field = DataSnapshot(
            columns={
                "x": Column(gx.ravel().tolist(), dtype="float", unit=p["x_unit"]),
                "y": Column(gy.ravel().tolist(), dtype="float", unit=p["y_unit"]),
                "u": Column(values["u"], dtype="float", unit=vu,
                            description=f"u(x, y) = {p['u']}"),
                "v": Column(values["v"], dtype="float", unit=vu,
                            description=f"v(x, y) = {p['v']}"),
            },
            provenance=Provenance(
                source=f"ieeVisualizer.templates.{self.name}@{self.version}",
                notes="Sampled from symbolic expressions on a regular grid (y outer, x inner); "
                      "computed, not measured.",
                extra={"expressions": {"u": p["u"], "v": p["v"]},
                       "constants": constants, "grid": [nx, ny],
                       "x_range": list(p["x_range"]), "y_range": list(p["y_range"]),
                       "sympy_version": sympy.__version__, "numpy_version": np.__version__},
            ),
        )
        meta = {
            "expressions": {"u": p["u"], "v": p["v"]},
            "parsed": {k: str(e) for k, e in exprs.items()},
            "grid_order": "row-major: y outer, x inner",
        }
        return field, meta

    def example(self) -> dict[str, Any]:
        return {
            "data": None,
            "params": {
                "u": "-y",
                "v": "x",
                "x_range": [-2.0, 2.0],
                "y_range": [-2.0, 2.0],
                "grid": 11,
                "title": "Rotation field",
            },
        }
