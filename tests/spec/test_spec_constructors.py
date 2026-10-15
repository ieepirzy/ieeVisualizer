"""Python construction of every model class behaves as it did before the
pydantic migration, when the classes were stdlib dataclasses: positional
arguments fill the ``__init__`` fields in ``dataclasses.fields`` order (a
mark's ``type`` is not an ``__init__`` field), bad calls raise the stdlib
``TypeError``, ``inspect.signature`` shows the ``__init__`` parameters, and
the descriptor classes take the containers callers used to pass."""

from __future__ import annotations

import abc
import dataclasses
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from ieeVisualizer import templates
from ieeVisualizer.artifacts.manifest import (
    DatasetRecord,
    FileEntry,
    RenderVariant,
    VisualizationArtifact,
)
from ieeVisualizer.render import default_registry
from ieeVisualizer.render.base import Capabilities, RenderRequest, RenderResult, RenderWarning
from ieeVisualizer.spec import (
    Encoding,
    FigureSpec,
    LineMark,
    ReferenceLine,
    SpecParseError,
    ValidationIssue,
)
from ieeVisualizer.spec._common import SpecModel
from ieeVisualizer.spec.figure import Annotation
from ieeVisualizer.spec.marks import Mark
from ieeVisualizer.templates.base import ColumnRequirement, Parameter

ROOT = Path(__file__).resolve().parents[2]


def _all_model_classes() -> list[type]:
    def subs(c):
        for s in c.__subclasses__():
            yield s
            yield from subs(s)

    return sorted({c for c in subs(SpecModel) if c.__module__.startswith("ieeVisualizer.")},
                  key=lambda c: (c.__module__, c.__name__))


def _init_names(cls) -> list[str]:
    return [f.name for f in dataclasses.fields(cls) if f.init]


def _harvest() -> dict[type, list]:
    """Instances of every model class reachable from the enumerable specs."""
    spec = importlib.util.spec_from_file_location("_ctor_spec_hashes",
                                                  ROOT / "tools" / "spec_hashes.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    found: dict[type, list] = {}

    def walk(v):
        if isinstance(v, SpecModel):
            found.setdefault(type(v), []).append(v)
            for f in dataclasses.fields(v):
                walk(getattr(v, f.name))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for build in tool._builders().values():
        s = build()
        if isinstance(s, FigureSpec):
            walk(s)
    return found


def _descriptor_instances() -> dict[type, list]:
    t = templates.get("timeseries")
    file = FileEntry("figure.png", "image/png", "sha256:ab", 12)
    variant = RenderVariant("png", file, "matplotlib", "1.0", {"format": "png"},
                            [{"code": "c", "message": "m", "path": "/p"}])
    record = DatasetRecord("snapshot", "sha256:cd", {"source": "s"}, {"x": {"dtype": "float"}},
                           {"kind": "query", "id": "q"}, True)
    return {
        ValidationIssue: [ValidationIssue("/a", "code", "message", "warning")],
        RenderWarning: [RenderWarning("code", "message", "/path")],
        RenderRequest: [RenderRequest("svg", 800, 600, "px", 96, "light", "matplotlib")],
        RenderResult: [RenderResult(b"x", "png", "image/png", "mpl", "1", [RenderWarning("c", "m")],
                                    RenderRequest(), {"name": "t"})],
        Capabilities: [a.capabilities for a in default_registry],
        Parameter: list(t.parameters),
        ColumnRequirement: list(t.columns),
        FileEntry: [file],
        RenderVariant: [variant],
        DatasetRecord: [record],
        VisualizationArtifact: [VisualizationArtifact(
            "id", "sha256:ef", "1.0", file, {"d": record}, [variant], {"numpy": "2"},
            "2026-01-01T00:00:00Z", "spec-id", "Title", {"k": 1}, file, {"name": "t"}, "1.0")],
        Mark: [Mark("line", "d", {"y": Encoding("v")}, "observed", "n", {"color": "red"})],
        Annotation: [Annotation("reference_line", "label", {"color": "red"})],
    }


INSTANCES = {**_harvest(), **_descriptor_instances()}


def _best(cls):
    """The instance whose adjacent init-field values differ most, so a
    one-place shift of the positional arguments cannot go unnoticed."""
    def score(obj):
        vals = [getattr(obj, n) for n in _init_names(cls)]
        return sum(a != b for a, b in zip(vals, vals[1:]))

    return max(INSTANCES[cls], key=score)


@pytest.mark.parametrize("cls", _all_model_classes(), ids=lambda c: c.__name__)
def test_positional_arguments_fill_the_init_fields_in_declaration_order(cls):
    assert cls in INSTANCES, f"no sample instance of {cls.__name__}; add one"
    obj = _best(cls)
    names = _init_names(cls)
    vals = [getattr(obj, n) for n in names]
    if len(names) > 1:  # the sample must be able to expose a shift
        assert any(a != b for a, b in zip(vals, vals[1:])), cls.__name__
    built = cls(*vals)
    assert [getattr(built, n) for n in names] == vals
    assert built == obj
    # every split between positional and keyword arguments agrees
    for k in range(len(names) + 1):
        assert cls(*vals[:k], **dict(zip(names[k:], vals[k:]))) == obj


def test_examples_from_the_review():
    m = LineMark("sales", {"y": "revenue"})
    assert (m.type, m.data, m.encoding) == ("line", "sales", {"y": Encoding("revenue")})
    r = ReferenceLine("target", {}, "y", 5)
    assert (r.type, r.label, r.style, r.axis, r.value) == ("reference_line", "target", {}, "y", 5)


@pytest.mark.parametrize("call, message", [
    (lambda: LineMark(*range(7)),
     "LineMark.__init__() takes from 1 to 6 positional arguments but 8 were given"),
    (lambda: LineMark("d", data="e"),
     "LineMark.__init__() got multiple values for argument 'data'"),
    (lambda: Parameter("p"),
     "Parameter.__init__() missing 2 required positional arguments: 'type' and 'description'"),
    (lambda: FileEntry("f", "t", "s", 1, 2),
     "FileEntry.__init__() takes 5 positional arguments but 6 were given"),
    (lambda: LineMark(type="bar"), "LineMark.__init__() got an unexpected keyword argument 'type'"),
    (lambda: FigureSpec(tittle="x"),
     "FigureSpec.__init__() got an unexpected keyword argument 'tittle'"),
])
def test_bad_calls_raise_the_stdlib_type_error(call, message):
    with pytest.raises(TypeError) as e:
        call()
    assert type(e.value) is TypeError
    # CPython names the class in these messages from 3.10 on.
    text = str(e.value)
    assert text.startswith(message) or text.startswith(message.split(".", 1)[1]), text


def _stdlib_twin(cls):
    """A plain stdlib dataclass with ``cls``'s name and ``__init__`` fields:
    what the class was before the migration, as far as binding a call goes."""
    fields = [(f.name, object, dataclasses.field(default=None))
              for f in dataclasses.fields(cls) if f.init]
    return dataclasses.make_dataclass(cls.__name__, fields)


@pytest.mark.parametrize("cls, kwargs", [
    (FigureSpec, {"tittle": "x"}),
    (LineMark, {"dta": "d"}),
    (Encoding, {"feld": "x"}),
    (RenderRequest, {"widht": 3}),
    (Parameter, {"name": "p", "type": "string", "description": "d", "alowed": ["a"]}),
])
def test_an_unknown_keyword_reads_as_on_this_python_before(cls, kwargs):
    """Word for word what the stdlib dataclass says on the running Python:
    CPython 3.13 appends "Did you mean 'title'?", earlier versions do not."""
    with pytest.raises(TypeError) as got:
        cls(**kwargs)
    with pytest.raises(TypeError) as want:
        _stdlib_twin(cls)(**kwargs)
    assert str(got.value) == str(want.value)
    if sys.version_info >= (3, 13):
        assert "Did you mean" in str(got.value)


def test_an_argument_error_wins_over_a_value_error():
    with pytest.raises(TypeError, match="unexpected keyword argument 'bogus'"):
        RenderRequest(width="5", bogus=1)
    with pytest.raises(TypeError, match="unexpected keyword argument 'bogus'"):
        FileEntry("f", "t", "s", "12", bogus=1)


def test_signature_lists_the_init_parameters():
    assert str(inspect.signature(LineMark)) == \
        "(data=None, encoding=<factory>, role='observed', name=None, style=<factory>)"
    for cls in _all_model_classes():
        assert list(inspect.signature(cls).parameters) == _init_names(cls), cls.__name__


def test_model_classes_mix_with_abc():
    class Abstract(LineMark, abc.ABC):
        @abc.abstractmethod
        def extra(self): ...

    class Concrete(Abstract):
        def extra(self):
            return 1

    with pytest.raises(TypeError):
        Abstract(data="d")
    assert Concrete(data="d").data == "d" and isinstance(Concrete(data="d"), LineMark)


def test_render_request_reports_problems_in_the_old_order():
    with pytest.raises(ValueError, match="unknown output format 'gif'"):
        RenderRequest(format="gif", units="cm", width=-1)
    with pytest.raises(ValueError, match="units must be 'in' or 'px', got 'cm'"):
        RenderRequest(units="cm", width=-1)
    with pytest.raises(ValueError, match="width must be a positive finite number"):
        RenderRequest(width=-1, dpi=0)


def test_descriptors_take_the_containers_callers_used_to_pass():
    assert Parameter("p", "string", "d", allowed=["a", "b"]).allowed == ("a", "b")
    assert ColumnRequirement("r", "p", ["float", "int"], "d").dtypes == ("float", "int")
    caps = Capabilities({"cartesian2d"}, ["line"], ("png",), unsupported={"polar": {"bar"}})
    assert caps.mark_types == frozenset({"line"}) and caps.unsupported == {
        "polar": frozenset({"bar"})}
    res = RenderResult(bytearray(b"ab"), "png", "image/png", "a", "1",
                       (RenderWarning("c", "m"),))
    assert res.content == b"ab" and type(res.content) is bytes
    assert res.warnings == [RenderWarning("c", "m")]
    assert RenderResult(memoryview(b"ab"), "png", "image/png", "a", "1").size_bytes == 2
    assert FileEntry("f", "t", "s", 10.0).size_bytes == 10.0
    assert Capabilities(set(), set(), set(), max_points=1e6).max_points == 1e6


@pytest.mark.parametrize("call, path", [
    (lambda: Parameter("p", "string", "d", allowed="ab"), "/allowed"),
    (lambda: ColumnRequirement("r", "p", "float", "d"), "/dtypes"),
    (lambda: FileEntry("f", "t", "s", True), "/size_bytes"),
    (lambda: RenderResult("text", "png", "image/png", "a", "1"), "/content"),
])
def test_wrong_types_are_still_refused(call, path):
    with pytest.raises(SpecParseError) as e:
        call()
    assert e.value.issues[0].path == path and e.value.issues[0].code == "invalid_type"
