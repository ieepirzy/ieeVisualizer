"""vector_field_2d expressions cannot make SymPy hang.

The static check refuses a power of two literal constants, but SymPy's
exact arithmetic can still be reached through a subexpression that only
becomes constant on evaluation, or through a caller constant. Each case runs
in a child process with a timeout so a regression fails instead of hanging.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ieeVisualizer.templates import TemplateInputError, get

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VF = get("vector_field_2d")

_CHILD = """
import json, sys, time
from ieeVisualizer.templates import TemplateInputError, get
vf = get("vector_field_2d")
params = {"u": sys.argv[1], "v": "x", "grid": 3, **json.loads(sys.argv[2])}
t = time.perf_counter()
out = {"check": [[i.path, i.code] for i in vf.check(None, **params) if i.severity == "error"]}
out["check_seconds"] = time.perf_counter() - t
t = time.perf_counter()
try:
    vf.build(None, **params)
    out["build"] = "built"
except TemplateInputError as e:
    out["build"] = [[i.path, i.code] for i in e.issues if i.severity == "error"]
out["build_seconds"] = time.perf_counter() - t
print(json.dumps(out))
"""


def _run_in_child(u: str, extra: dict | None = None) -> dict:
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run([sys.executable, "-c", _CHILD, u, json.dumps(extra or {})],
                          capture_output=True, text=True, timeout=30, env=env, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("u,extra", [
    ("(0*x+9)**999999999", None),                 # 0*x+9 evaluates to 9
    ("k**(x-x+999999999)", {"constants": {"k": 9}}),  # constant substituted first
    ("x**(x-x+999999999)", None),
    ("exp(999999999*log(k))", {"constants": {"k": 9}}),  # exp(c*log(b)) -> b**c
    ("(x-x+9)**(x-x+9)**(x-x+9)", None),
    ("k**999", {"constants": {"k": 9}}),          # ~950 digits, beyond float64
])
def test_explosive_expression_fails_fast_in_check_and_build(u, extra):
    out = _run_in_child(u, extra)
    expected = [["/params/u", "expression_too_large"]]
    assert out["check"] == expected, out
    assert out["build"] == expected, out
    assert out["check_seconds"] < 2.0 and out["build_seconds"] < 2.0, out


def test_ordinary_expressions_and_constants_still_build():
    spec = VF.build(u="k*x**2 - y**0.5*0 + 2**x", v="sin(x)*exp(-y/5)",
                    constants={"k": 0.5}, x_range=[0, 1], y_range=[0, 1], grid=2)
    us = spec.datasets["field"].columns["u"].values
    assert us == pytest.approx([1.0, 2.5, 1.0, 2.5])


def test_check_reports_and_build_raises_the_same_structured_issue():
    issues = VF.check(None, u="(0*x+9)**2000", v="x")
    assert [(i.path, i.code) for i in issues if i.severity == "error"] == [
        ("/params/u", "expression_too_large")]
    with pytest.raises(TemplateInputError) as e:
        VF.build(None, u="(0*x+9)**2000", v="x")
    assert [(i.path, i.code) for i in e.value.issues] == [("/params/u", "expression_too_large")]


@pytest.mark.parametrize("u,constants", [
    ("x/0", {}), ("1/0", {}), ("x + log(0)", {}), ("tan(pi/2)*x", {}), ("x*0**-1", {}),
    ("x/k", {"k": 0.0}),
])
def test_undefined_constant_is_an_issue_never_an_exception(u, constants):
    params = {"u": u, "v": "y", "x_range": [-1, 1], "y_range": [-1, 1], "grid": 5,
              "constants": constants}
    issues = VF.check(None, **params)  # must not raise
    assert [(i.path, i.code) for i in issues if i.severity == "error"] == [
        ("/params/u", "invalid_expression")]
    with pytest.raises(TemplateInputError) as e:
        VF.build(None, **params)
    assert [(i.path, i.code) for i in e.value.issues if i.severity == "error"] == [
        ("/params/u", "invalid_expression")]


def test_lambdify_failure_is_an_issue(monkeypatch):
    import sympy

    def broken(*a, **k):
        raise KeyError("ComplexInfinity")

    monkeypatch.setattr(sympy, "lambdify", broken)
    params = {"u": "x", "v": "y", "x_range": [-1, 1], "y_range": [-1, 1], "grid": 5}
    with pytest.raises(TemplateInputError) as e:
        VF.build(None, **params)
    assert {(i.path, i.code) for i in e.value.issues} == {
        ("/params/u", "invalid_expression"), ("/params/v", "invalid_expression")}



@pytest.mark.parametrize("params", [
    {"constants": {"a": float("nan")}}, {"x_range": [0.0, float("inf")]},
])
def test_non_finite_numeric_parameters_are_refused_by_check(params):
    full = {"u": "a*x" if "constants" in params else "x", "v": "y", "x_range": [-1, 1],
            "y_range": [-1, 1], "grid": 5, **params}
    key = next(iter(params))
    assert [(i.path, i.code) for i in VF.check(None, **full) if i.severity == "error"] == [
        (f"/params/{key}", "invalid_parameter")]
    with pytest.raises(TemplateInputError):
        VF.build(None, **full)
