"""The pydantic model classes behave as the stdlib dataclasses on ``main``
(cc109d5) did, for the cases the review of PR #18 reproduced.

Every expectation here was taken from running the same call on a checkout of
``main``; ``main_signatures.json`` was recorded there (``inspect.signature``
of each class and of its ``__init__``, and ``__doc__``).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import time
from pathlib import Path

import numpy as np
import pytest

from ieeVisualizer.artifacts.manifest import FileEntry, RenderVariant
from ieeVisualizer.render.base import Capabilities, RenderRequest, RenderResult, RenderWarning
from ieeVisualizer.spec import (
    FigureSpec,
    Legend,
    LineMark,
    SpecParseError,
    TextAnnotation,
    ValidationIssue,
    apply_patch,
    from_dict,
    from_json,
)
from ieeVisualizer.spec._common import SpecModel, model
from ieeVisualizer.spec._model import Str, WireShape, parse_model
from ieeVisualizer.templates.base import ColumnRequirement, Parameter

HERE = Path(__file__).resolve().parent


def issues(call):
    with pytest.raises(SpecParseError) as e:
        call()
    return [(i.path, i.code) for i in e.value.issues]


# --- subclasses and __init__ ---------------------------------------------------


def test_a_subclass_with_its_own_init_constructs():
    class Hidden(Legend):
        def __init__(self, hidden_title):
            super().__init__(show=False, title=hidden_title)

    class Rev(LineMark):
        def __init__(self, column):
            super().__init__(data="d", encoding={"y": column})

    h = Hidden("x")
    assert (type(h), h.show, h.position, h.title) == (Hidden, False, "auto", "x")
    r = Rev("revenue")
    assert (r.type, r.data, r.encoding["y"].field) == ("line", "d", "revenue")


def test_positional_super_init_binds_the_init_fields():
    class Named(LineMark):
        def __init__(self, data, **kw):
            super().__init__(data, **kw)

    m = Named("ds", encoding={"x": "a"})
    assert (m.type, m.data, m.encoding["x"].field) == ("line", "ds", "a")

    class T(TextAnnotation):
        def __init__(self, label, text):
            super().__init__(label, {}, text)

    t = T("lbl", "hello")
    assert (t.type, t.label, t.style, t.text) == ("text", "lbl", {}, "hello")


def test_calling_init_again_rebinds_positionally():
    m = LineMark("d")
    m.__init__("x2")
    assert (m.type, m.data) == ("line", "x2")
    assert LineMark.__init__.__code__.co_varnames[:2] == ("self", "data")


# --- constructors accept and store what main did -------------------------------


def test_descriptors_accept_what_main_accepted():
    assert ValidationIssue("/a", "c", "m", "fatal").severity == "fatal"
    assert ValidationIssue(1, "c", "m").path == 1
    caps = Capabilities(set(), set(), set(), three_d=1, max_points=np.int64(5),
                        max_bins=2.5)
    assert caps.three_d == 1 and type(caps.max_points) is np.int64 and caps.max_bins == 2.5
    assert type(Capabilities({1}, [], (), animation=np.bool_(True)).animation) is np.bool_
    assert type(FileEntry("f", "t", "s", np.int64(3)).size_bytes) is np.int64
    assert RenderWarning("c", "m", None).path is None
    v = RenderVariant("png", FileEntry("f", "t", "s", 1), "a", "1",
                      warnings=[{"path": None, "code": 1}])
    assert v.warnings == [{"path": None, "code": 1}]
    assert RenderResult(b"x", "png", "image/png", "a", "1", theme={"version": None}).theme == {
        "version": None}
    assert Parameter("p", "string", "d", allowed={"a"}).allowed == {"a"}
    assert Parameter("p", "strng", "d", required=True, default="x").type == "strng"
    assert ColumnRequirement("r", "p", "float", "d", unit="sometimes").dtypes == "float"


def test_constructors_store_the_callers_objects():
    unsupported = {"polar": {"bar"}}
    sets = [{"cartesian2d"}, ["line"], ("png",)]
    caps = Capabilities(*sets, unsupported=unsupported)
    assert caps.unsupported is unsupported and caps.unsupported["polar"] == {"bar"}
    assert [caps.coordinate_systems, caps.mark_types, caps.output_formats] == sets
    assert caps.mark_types is sets[1]
    w = (RenderWarning("c", "m"),)
    req = RenderRequest()
    content = bytearray(b"ab")
    res = RenderResult(content, "png", "image/png", "a", "1", w, req)
    assert res.warnings is w and res.request is req and res.content is content
    allowed = ["a", "b"]
    assert Parameter("p", "string", "d", allowed=allowed).allowed is allowed
    layers = [LineMark("d")]
    assert FigureSpec(layers=layers).layers is layers


def test_default_factories_run_once_per_construction():
    calls = []

    def factory():
        calls.append(1)
        return []

    @model
    class Counted(SpecModel):
        __wire__ = WireShape("a counted")

        items: Str("items") = dataclasses.field(default_factory=factory)

    Counted()
    assert len(calls) == 1


def test_a_post_init_exception_keeps_its_own_context():
    with pytest.raises(ValueError) as e:
        RenderRequest(format="gif")
    assert (e.value.__cause__, e.value.__context__, e.value.__suppress_context__) == (
        None, None, False)


# --- signatures and docs -------------------------------------------------------


def _frozensets_sorted(text: str) -> str:
    """Set reprs are ordered by string hash, which varies between runs."""
    return re.sub(r"frozenset\(\{([^}]*)\}\)",
                  lambda m: "frozenset({" + ", ".join(sorted(m.group(1).split(", "))) + "})",
                  text)


def _model_classes() -> dict[str, type]:
    def subs(c):
        for s in c.__subclasses__():
            yield s
            yield from subs(s)

    return {f"{c.__module__}.{c.__qualname__}": c for c in subs(SpecModel)
            if c.__module__.startswith("ieeVisualizer.")}


MAIN_SIGNATURES = json.loads((HERE / "main_signatures.json").read_text(encoding="utf-8"))


def test_every_model_class_has_a_recorded_signature():
    assert set(_model_classes()) == set(MAIN_SIGNATURES)


@pytest.mark.parametrize("name", sorted(MAIN_SIGNATURES))
def test_signature_and_doc_are_mains(name):
    cls, want = _model_classes()[name], MAIN_SIGNATURES[name]
    assert _frozensets_sorted(str(inspect.signature(cls))) == \
        _frozensets_sorted(want["signature"])
    assert _frozensets_sorted(str(inspect.signature(cls.__init__))) == \
        _frozensets_sorted(want["init"])
    # Python 3.13 strips docstring indentation at compile time; compare cleaned.
    assert inspect.cleandoc(cls.__doc__) == inspect.cleandoc(want["doc"])


# --- parse errors: lone surrogates, raw exceptions, first issue wins -------------


@pytest.mark.parametrize("call, expected", [
    (lambda: from_json('{"schema_version":"1.0","legend":{"\\ud800":1}}'),
     [("/legend/\ud800", "unknown_field")]),
    (lambda: from_json('{"schema_version":"1.0","axes":{"x":{"lab\\udc00el":"a"}}}'),
     [("/axes/x/lab\udc00el", "unknown_field")]),
    (lambda: from_json('{"schema_version":"1.0","datasets":{"d":{"type":"snapshot",'
                       '"columns":{"\\ud800":1}}}}'),
     [("/datasets/d/columns/\ud800", "invalid_type")]),
    (lambda: from_dict({"schema_version": "1.0", "metadata": {"\ud800": float("nan")}}),
     [("/metadata/\ud800", "non_finite_number")]),
    (lambda: from_dict({"schema_version": "1.0", "layers": [
        {"type": "line", "style": {"a": {"\udc80": object()}}}]}),
     [("/layers/0/style/a/\udc80", "invalid_type")]),
    (lambda: from_dict({"schema_version": "1.0", "layers": [
        {"type": "line", "encoding": {"\ud800": {"field": 5}}}]}),
     [("/layers/0/encoding/\ud800/field", "invalid_type")]),
    (lambda: from_dict({"schema_version": "1.0", "datasets": {"d": {
        "type": "snapshot", "columns": {"\ud800": {"values": [1, "a"]}}}}}),
     [("/datasets/d/columns/\ud800/values", "invalid_values")]),
    (lambda: apply_patch(FigureSpec(), [{"op": "add", "path": "/legend/\ud800", "value": 1}]),
     [("/legend/\ud800", "unknown_field")]),
])
def test_lone_surrogate_keys_give_mains_issue(call, expected):
    assert issues(call) == expected


def test_a_post_init_error_text_is_not_encoded_by_pydantic():
    @model
    class Picky(SpecModel):
        __wire__ = WireShape("a picky object")
        __post_init_issue__ = ("invalid_values", "/name")

        name: Str("name") = ""

        def __post_init__(self):
            if self.name:
                raise ValueError(f"bad {self.name}")

    with pytest.raises(SpecParseError) as e:
        parse_model(Picky, {"name": "\ud800"})
    assert [(i.path, i.code, i.message) for i in e.value.issues] == [
        ("/name", "invalid_values", "bad \ud800")]


def test_a_non_spec_error_in_a_field_parser_propagates_as_on_main():
    with pytest.raises(ValueError) as e:
        from_dict({"schema_version": "1.0", "datasets": {"observed": {"type": np.array([1, 2])}}})
    assert type(e.value) is ValueError and "truth value" in str(e.value)


class _Watched(dict):
    """A JSON object that records whether anything read its items."""

    read = False

    def items(self):
        type(self).read = True
        return super().items()


@pytest.mark.parametrize("bad", [{"title": 5}, {"caption": 5}, {"id": 5},
                                 {"coordinate_system": 5}])
def test_parsing_stops_at_the_first_problem(bad):
    _Watched.read = False
    datasets = _Watched(d={"type": "snapshot", "columns": {"x": {"values": [1.0]}}})
    with pytest.raises(SpecParseError) as e:
        from_dict({"schema_version": "1.0", "datasets": datasets, **bad})
    assert e.value.issues[0].path == "/" + next(iter(bad))
    assert not _Watched.read  # datasets come later in the parse order: never read


def test_an_early_error_is_cheap_however_big_the_rest():
    big = {"d": {"type": "snapshot", "columns": {"x": {"values": [0.5] * 500_000}}}}
    t0 = time.perf_counter()
    with pytest.raises(SpecParseError):
        from_dict({"schema_version": "1.0", "title": 5, "datasets": big})
    assert time.perf_counter() - t0 < 0.1

