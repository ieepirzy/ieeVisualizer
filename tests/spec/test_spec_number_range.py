"""Integers beyond the float64 range (e.g. ``10**400``).

JSON has no limit on integer size and Python ints have none either, but no
reader that parses numbers as doubles can hold such a value. Declared-numeric
fields refuse it with a structured ``number_out_of_range`` error (never a raw
``OverflowError``): on construction and on parse it is a ``SpecParseError``
carrying the pointer; set after construction, ``validate()`` reports it.
"""

import pytest

from ieeVisualizer.spec import (
    Axis,
    Column,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    HistogramMark,
    LineMark,
    QuiverMark,
    ReferenceBand,
    ReferenceLine,
    SpecParseError,
    TextAnnotation,
    from_dict,
    from_json,
    validate,
)

BIG = 10 ** 400


def _issue(exc_info):
    (issue,) = exc_info.value.issues
    return issue.path, issue.code


@pytest.mark.parametrize(
    "build, path",
    [
        (lambda b: Axis(limits=[0, b]), "/limits/1"),
        (lambda b: HistogramMark(bin_edges=[0.0, 1.0, b]), "/bin_edges/2"),
        (lambda b: HistogramMark(bins=b), "/bins"),
        (lambda b: QuiverMark(scale=b), "/scale"),
        (lambda b: FunctionMark(expression="x", domain={"x": [b, 1]}), "/domain/x/0"),
        (lambda b: FunctionMark(expression="a*x", domain={"x": [0, 1]}, parameters={"a": b}),
         "/parameters/a"),
        (lambda b: FunctionMark(expression="x", domain={"x": [0, 1]}, samples=b), "/samples"),
        (lambda b: TextAnnotation(text="t", x=1, y=b), "/y"),
        (lambda b: ReferenceLine(value=b), "/value"),
        (lambda b: ReferenceBand(start=0, end=b), "/end"),
        (lambda b: Column([1.5, b]), "/values/1"),
        (lambda b: Column([1, b], dtype="float"), "/values/1"),
    ],
)
@pytest.mark.parametrize("big", [BIG, -BIG], ids=["positive", "negative"])
def test_construction_refuses_out_of_range_numbers(build, path, big):
    with pytest.raises(SpecParseError) as e:
        build(big)
    assert _issue(e) == (path, "number_out_of_range")
    assert isinstance(e.value, ValueError)


def _fn_dict(**kw):
    layer = {"type": "function", "expression": "x", "domain": {"x": [0, 1]}}
    layer.update(kw)
    return {"schema_version": "1.0", "layers": [layer]}


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda d: d.update(axes={"x": {"limits": [BIG, 1]}}), "/axes/x/limits/0"),
        (lambda d: d["layers"][0]["domain"].update(x=[0, BIG]), "/layers/0/domain/x/1"),
        (lambda d: d["layers"][0].update(parameters={"a": -BIG}), "/layers/0/parameters/a"),
        (lambda d: d["layers"][0].update(samples=BIG), "/layers/0/samples"),
        (lambda d: d.update(layers=[{"type": "histogram", "bin_edges": [0, BIG]}]),
         "/layers/0/bin_edges/1"),
        (lambda d: d.update(layers=[{"type": "histogram", "bins": BIG}]), "/layers/0/bins"),
        (lambda d: d.update(layers=[{"type": "quiver", "scale": BIG}]), "/layers/0/scale"),
        (lambda d: d.update(annotations=[{"type": "text", "text": "t", "x": BIG, "y": 0}]),
         "/annotations/0/x"),
        (lambda d: d.update(annotations=[{"type": "reference_band", "start": 0, "end": BIG}]),
         "/annotations/0/end"),
        (lambda d: d.update(datasets={"d": {"type": "snapshot",
                                            "columns": {"v": {"values": [0.5, BIG]}}}}),
         "/datasets/d/columns/v/values/1"),
    ],
)
def test_parse_refuses_out_of_range_numbers_with_path(mutate, path):
    d = _fn_dict()
    mutate(d)
    with pytest.raises(SpecParseError) as e:
        from_dict(d)
    assert _issue(e) == (path, "number_out_of_range")


def test_from_json_refuses_an_out_of_range_integer_literal():
    text = '{"schema_version":"1.0","axes":{"y":{"limits":[0,1%s]}}}' % ("0" * 400)
    with pytest.raises(SpecParseError) as e:
        from_json(text)
    assert _issue(e) == ("/axes/y/limits/1", "number_out_of_range")


def _spec():
    return FigureSpec(
        datasets={"d": DataSnapshot(columns={"a": Column([1, 2]), "b": Column([1, 2])})},
        layers=[LineMark(data="d", encoding={"x": "a", "y": "b"}),
                HistogramMark(data="d", encoding={"x": "a"}, bin_edges=[0.0, 1.0, 2.0]),
                QuiverMark(scale=1.0),
                FunctionMark(expression="a*x", domain={"x": [0, 1]}, parameters={"a": 1})],
        axes={"y": Axis(limits=[0, 10])},
        annotations=[TextAnnotation(text="t", x=1, y=1), ReferenceLine(value=1)],
    )


@pytest.mark.parametrize(
    "mutate, path",
    [
        (lambda s: s.axes["y"].limits.__setitem__(1, BIG), "/axes/y/limits/1"),
        (lambda s: s.layers[1].bin_edges.__setitem__(2, BIG), "/layers/1/bin_edges/2"),
        (lambda s: setattr(s.layers[1], "bins", BIG), "/layers/1/bins"),
        (lambda s: setattr(s.layers[2], "scale", -BIG), "/layers/2/scale"),
        (lambda s: s.layers[3].domain["x"].__setitem__(0, -BIG), "/layers/3/domain/x/0"),
        (lambda s: s.layers[3].parameters.__setitem__("a", BIG), "/layers/3/parameters/a"),
        (lambda s: setattr(s.layers[3], "samples", BIG), "/layers/3/samples"),
        (lambda s: setattr(s.annotations[0], "x", BIG), "/annotations/0/x"),
        (lambda s: setattr(s.annotations[1], "value", BIG), "/annotations/1/value"),
        (lambda s: s.datasets["d"].columns["b"].values.__setitem__(1, BIG),
         "/datasets/d/columns/b/values/1"),
    ],
)
def test_validate_reports_out_of_range_numbers_set_after_construction(mutate, path):
    spec = _spec()
    before = {(i.path, i.code, i.severity) for i in validate(spec)}
    assert not any(code == "number_out_of_range" for _, code, _ in before)
    mutate(spec)
    after = {(i.path, i.code, i.severity) for i in validate(spec)}
    assert after - before == {(path, "number_out_of_range", "error")}


def test_an_int_column_beyond_float64_parses_and_validate_reports_it():
    # Columns store exact integers of any size (JSON has no limit); one beyond
    # float64 is a validation error, not a crash.
    d = {"schema_version": "1.0",
         "datasets": {"d": {"type": "snapshot",
                            "columns": {"v": {"values": [1, -BIG]}}}}}
    spec = from_dict(d)
    issues = [(i.path, i.code) for i in validate(spec)]
    assert ("/datasets/d/columns/v/values/1", "number_out_of_range") in issues


def test_a_number_at_the_float64_edge_is_accepted():
    assert Axis(limits=[0, 10 ** 308]).limits == [0.0, 1e308]
    spec = FigureSpec(axes={"y": Axis(limits=[-(10 ** 308), 0])})
    assert [i.code for i in validate(spec)] == ["no_layers"]


def test_an_integer_beyond_the_str_digit_limit_still_gets_a_message():
    with pytest.raises(SpecParseError) as e:
        Axis(limits=[0, 10 ** 5000])
    assert "a very large integer" in str(e.value)
