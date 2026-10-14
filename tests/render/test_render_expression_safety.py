"""A function mark's expression cannot make SymPy hang or crash.

Each refusal runs in a child process with a timeout, so a regression to the
old behaviour (exact big-integer arithmetic that never returns) fails the
test instead of hanging the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from ieeVisualizer.render import RenderError, build_figure
from ieeVisualizer.render.mpl.function import parse
from ieeVisualizer.spec import Axis, FigureSpec, FunctionMark

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CHILD = """
import json, sys, time
from ieeVisualizer.render import RenderError
from ieeVisualizer.render.mpl.function import parse
from ieeVisualizer.spec import FunctionMark
m = FunctionMark(expression=sys.argv[1], variables=["x"], domain={"x": [0.0, 1.0]},
                 parameters=json.loads(sys.argv[2]))
t = time.perf_counter()
try:
    parse(m, "/layers/0")
    out = {"issues": []}
except RenderError as e:
    out = {"issues": [[i.path, i.code] for i in e.issues]}
except Exception as e:
    out = {"raised": type(e).__name__}
out["seconds"] = time.perf_counter() - t
print(json.dumps(out))
"""


def _parse_in_child(expression: str, parameters: dict | None = None) -> dict:
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run([sys.executable, "-c", _CHILD, expression,
                           json.dumps(parameters or {})],
                          capture_output=True, text=True, timeout=30, env=env, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("expression,parameters", [
    ("9^9^9", None),
    ("x*9^9^10", None),
    ("(x)^(9^9^9)", None),
    ("gamma(10^6)", None),
    ("x^(x-x+999999999)", None),          # the exponent becomes constant on evaluation
    ("(0*x+9)^999999999", None),          # 0*x+9 evaluates to 9
    ("k^(x-x+999999999)", {"k": 9.0}),    # parameter substituted before the power
    ("exp(10^9*log(9))", None),           # SymPy rewrites exp(c*log(b)) to b**c
    ("7^x*7^(10^9-x)", None),             # Mul combines the powers into 7**(10**9)
    ("floor(1e999999999)", None),         # the literal alone expands exactly
    ("9^999", None),                      # ~950 digits, beyond float64
])
def test_explosive_expression_is_refused_fast(expression, parameters):
    out = _parse_in_child(expression, parameters)
    assert out.get("issues") == [["/layers/0/expression", "expression_too_large"]], out
    assert out["seconds"] < 2.0, out


@pytest.mark.parametrize("expression", ["x,x", "(x, x)"])
def test_non_expression_is_a_render_error_not_attribute_error(expression):
    m = FunctionMark(expression=expression, variables=["x"], domain={"x": [0.0, 1.0]})
    with pytest.raises(RenderError) as e:
        parse(m, "/layers/0")
    assert [(i.path, i.code) for i in e.value.issues] == [
        ("/layers/0/expression", "invalid_expression")]


def test_oversized_expression_is_refused():
    m = FunctionMark(expression="+".join(["x"] * 600), variables=["x"],
                     domain={"x": [0.0, 1.0]})
    with pytest.raises(RenderError) as e:
        parse(m, "/layers/0")
    assert e.value.issues[0].code == "expression_too_large"


@pytest.mark.parametrize("expression,parameters,expect", [
    ("x**2", {}, lambda x: x ** 2),
    ("x^2", {}, lambda x: x ** 2),
    ("sin(x)*exp(-x/5)", {}, lambda x: np.sin(x) * np.exp(-x / 5)),
    ("2**x", {}, lambda x: 2.0 ** x),
    ("x**0.5", {}, np.sqrt),
    ("a*x^3 + b", {"a": 2.0, "b": -1.0}, lambda x: 2 * x ** 3 - 1),
    ("2^1000*x/2^1000", {}, lambda x: x),
    ("Abs(x-0.5)", {}, lambda x: np.abs(x - 0.5)),
    ("x^1000", {}, lambda x: x ** 1000),
])
def test_ordinary_expressions_still_draw(expression, parameters, expect):
    spec = FigureSpec(
        layers=[FunctionMark(expression=expression, variables=["x"], domain={"x": [0.0, 1.0]},
                             parameters=parameters, samples=11)],
        axes={"x": Axis(label="x"), "y": Axis(label="y")})
    (line,) = build_figure(spec).layer_artists[0]
    x = np.linspace(0.0, 1.0, 11)
    np.testing.assert_allclose(np.ma.asarray(line.get_ydata()), expect(x), rtol=1e-9)


def test_numpy_failure_on_a_huge_constant_is_a_render_error():
    spec = FigureSpec(
        layers=[FunctionMark(expression="exp(10^300)+x", variables=["x"],
                             domain={"x": [0.0, 1.0]})],
        axes={"x": Axis(label="x"), "y": Axis(label="y")})
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code == "function_not_drawable"


@pytest.mark.parametrize("value", [1.7e308, 5e-324])
def test_any_float64_parameter_is_accepted(value):
    m = FunctionMark(expression="a*x", variables=["x"], domain={"x": [0.0, 1.0]},
                     parameters={"a": value})
    expr, _ = parse(m, "/layers/0")
    assert {s.name for s in expr.free_symbols} == {"x"}
