"""The C-level fast path for plain float columns (normalize_values,
validate, the renderer's numbers()) gives exactly what the per-value path
gives, and falls back to it for anything it does not cover."""

import math

import pytest

from ieeVisualizer.spec import Column, FigureSpec, validate
from ieeVisualizer.spec.data import DataSnapshot, normalize_values


@pytest.mark.parametrize("values, expected, dtype", [
    ([1.0, -0.0, 2.5], [1.0, 0.0, 2.5], "float"),
    ([None, -0.0, 3.0], [None, 0.0, 3.0], "float"),
    ([1.0, float("nan"), 2.0], [1.0, None, 2.0], "float"),  # nan is missing
    ([None, None], [None, None], None),  # all-null: dtype not guessed
    ([1.0, 2], [1.0, 2.0], "float"),  # an int among floats: slow path
    ([1e308, 1e308], [1e308, 1e308], "float"),  # sum overflows: slow path, still valid
])
def test_fast_path_matches_the_per_value_rules(values, expected, dtype):
    out, got_dtype = normalize_values(values, None)
    assert out == expected and got_dtype == dtype
    assert [math.copysign(1, v) for v in out if v is not None] == [
        1.0 for v in out if v is not None]  # no -0.0 survives


def test_infinity_is_still_refused_and_reported():
    with pytest.raises(ValueError, match="infinities"):
        normalize_values([1.0, float("inf")], None)
    col = Column([1.0, 2.0])
    col.values[1] = float("-inf")  # changed after construction
    spec = FigureSpec(datasets={"d": DataSnapshot(columns={"c": col})})
    assert "non_finite_number" in [i.code for i in validate(spec)]


def _counting(monkeypatch, module, name):
    real = getattr(module, name)
    hits = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        hits.append(out)
        return out

    monkeypatch.setattr(module, name, wrapper)
    return hits


def test_the_fast_path_is_taken_for_plain_float_columns(monkeypatch):
    """Deleting the fast path (in construction, validation or the renderer)
    fails here, not only in a benchmark."""
    import importlib

    data = importlib.import_module("ieeVisualizer.spec.data")
    val = importlib.import_module("ieeVisualizer.spec.validate")  # the name is shadowed

    built = _counting(monkeypatch, data, "_plain_floats")
    checked = _counting(monkeypatch, val, "finite_plain_floats")
    col = Column([0.5, None, -0.0, 2.0])
    assert built and built[-1] == [0.5, None, 0.0, 2.0]  # fast branch produced it
    spec = FigureSpec(datasets={"d": DataSnapshot(columns={"c": col})})
    validate(spec)
    assert checked == [True]


def test_the_renderer_reads_plain_float_columns_without_a_per_value_loop(monkeypatch):
    import ieeVisualizer.render.mpl.data as rdata

    hits = _counting(monkeypatch, rdata, "finite_plain_floats")
    monkeypatch.setattr(rdata, "to_float", lambda *a: pytest.fail("per-value path used"))
    out = rdata.numbers(Column([1.0, None, 3.0]), "/x")
    assert hits == [True]
    assert out.tolist() == [1.0, None, 3.0]
