"""Pydantic plumbing shared by the model classes (see ``docs/MODELS.md``).

Every spec class is a :func:`pydantic.dataclasses.dataclass`. Its field
annotations declare the *wire type* of each field (how :func:`from_dict`
reads it) through the factories below (:func:`Str`, :func:`OptNumber`,
:func:`Nested`, :func:`wire`, ...), so a class's annotations are the one place
its JSON shape is written down.

Two paths:

* **construct** (``FigureSpec(...)``, ``Column(...)`` from Python): the
  stdlib dataclass ``__init__`` (``_common._ModelMeta``). Nothing is checked
  or converted beyond what the classes' ``__post_init__`` hooks did before
  the migration: a value of the wrong type is stored as given and
  :func:`~ieeVisualizer.spec.validate` reports it.
* **parse** (:func:`parse_model`, used by ``from_dict``): the keys are checked
  (unknown, missing), then each present field is read by its parser - in the
  order the pre-migration parser read them, stopping at the first problem -
  and pydantic builds the object from the parsed values (defaults, default
  factories, ``__post_init__``).

A field parser raises :class:`~ieeVisualizer.spec.SpecParseError` with a
pointer *relative to its field*; :func:`parse_model` prepends the field's
pointer, so nested objects compose into an RFC-6901 pointer from the parsed
document's root. Any other exception a parser raises propagates unchanged,
as it did from the pre-migration parser. A ``__post_init__`` exception
during parsing is re-raised as the original object (or, for a class with
``__post_init_issue__``, as that issue), never as a pydantic
``ValidationError``.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Annotated, Any, Callable

from pydantic import TypeAdapter, ValidationError

from ._common import (
    POST_INIT_CATCH,
    SPEC_CONFIG,
    SpecError,
    SpecModel,
    SpecParseError,
    ValidationIssue,
    Wire,
    check_keys,
    copy_json,
    expect_bool,
    expect_dict,
    expect_int,
    expect_list,
    expect_number,
    expect_str,
    model,
    ptr,
)

# --------------------------------------------------------------------------
# Field types. ``wire(parse, text)`` is ``Annotated[Any, Wire(parse, text)]``:
# ``parse(value)`` reads the JSON value and raises SpecParseError with paths
# relative to the field; ``text`` is the Python type as signatures show it.
# pydantic sees ``Any``: it neither checks nor converts the parsed value.
# --------------------------------------------------------------------------


def wire(parse: Callable[[Any], Any], text: str) -> Any:
    return Annotated[Any, Wire(parse, text)]


def Str(what: str) -> Any:  # noqa: N802 - a type factory, used in annotations
    return wire(lambda v: expect_str(v, "", what), "str")


def OptStr(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_str(v, "", what, optional=True), "str | None")


def Bool(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_bool(v, "", what), "bool")


def Int(what: str) -> Any:  # noqa: N802
    """An integer; an integer-valued float such as ``200.0`` reads as ``200``."""
    return wire(lambda v: expect_int(v, "", what), "int")


def OptInt(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_int(v, "", what, optional=True), "int | None")


def OptNumber(what: str) -> Any:  # noqa: N802
    """A finite number stored as a canonical float, or null."""
    return wire(lambda v: expect_number(v, "", what, optional=True), "float | None")


def JsonObject(what: str) -> Any:  # noqa: N802
    """Free-form JSON object (style, metadata, provenance extra), deep-copied."""
    return wire(lambda v: copy_json(expect_dict(v, "", what), "", what), "dict[str, Any]")


def Nested(cls: Any) -> Any:  # noqa: N802
    """A nested model object, parsed by its own class (see :func:`parse_model`)."""
    return wire(lambda v: parse_model(cls, v), cls.__name__)


def at(pointer: str, parse: Callable[..., Any], *args: Any) -> Any:
    """``parse(*args)``, with ``pointer`` prepended to the path of any
    :class:`SpecParseError` it raises (paths are relative to what is parsed).
    Other exceptions propagate unchanged."""
    try:
        return parse(*args)
    except SpecParseError as e:
        raise SpecParseError([ValidationIssue(pointer + i.path, i.code, i.message, i.severity)
                              for i in e.issues]) from None


def parse_object(value: Any, what: str, item: Callable[[str, Any], Any]) -> dict[str, Any]:
    """A JSON object whose values are parsed by ``item(key, value)``."""
    return {k: at(ptr(k), item, k, v) for k, v in expect_dict(value, "", what).items()}


def parse_array(value: Any, what: str, item: Callable[[Any], Any]) -> list[Any]:
    """A JSON array whose items are parsed by ``item(value)``."""
    return [at(ptr(i), item, v) for i, v in enumerate(expect_list(value, "", what))]


# --------------------------------------------------------------------------
# Model classes.
# --------------------------------------------------------------------------


class WireShape:
    """How a model class appears on the wire. Set as ``__wire__`` on the class.

    ``what`` names the object in messages (a ``str.format`` template whose
    ``{name}`` is the key the object is stored under, e.g. ``"column
    {name!r}"``); ``what_keys`` (default ``what``) is used in unknown/missing
    key messages; ``required`` keys must be present; ``drop`` keys are allowed
    on the wire but are not ``__init__`` arguments (a dataset's ``type``, or a
    mark's, which its class fixes); ``order`` is the order in which fields
    are read, so the first problem of an object is the one the pre-migration
    parser reported (default: declaration order; fields not listed follow in
    declaration order); ``pre`` runs on the raw dict before the key check
    (``schema_version``).
    """

    def __init__(self, what: str, *, what_keys: str | None = None, required: tuple = (),
                 drop: tuple = (), order: tuple = (),
                 pre: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.what, self.what_keys = what, what_keys or what
        self.required, self.drop, self.order, self.pre = required, drop, order, pre


@functools.cache
def wire_keys(cls: Any) -> tuple[str, ...]:
    """Keys a serialized ``cls`` object may carry."""
    names = tuple(f.name for f in dataclasses.fields(cls))
    return names + tuple(k for k in cls.__wire__.drop if k not in names)


@functools.cache
def field_parsers(cls: Any) -> tuple[tuple[str, str, Callable[[Any], Any]], ...]:
    """``(name, pointer, parse)`` of every wire field of ``cls``, in read order."""
    shape: WireShape = cls.__wire__
    names = [f.name for f in dataclasses.fields(cls) if f.name not in shape.drop]
    names = [n for n in shape.order if n in names] + [n for n in names if n not in shape.order]
    out = []
    for name in names:
        meta = cls.__pydantic_fields__[name].metadata
        (w,) = [m for m in meta if isinstance(m, Wire)]
        out.append((name, ptr(name), w.parse))
    return tuple(out)


def _check_wire_shape(cls: Any, data: Any, name: Any) -> dict[str, Any]:
    shape: WireShape = cls.__wire__
    d = expect_dict(data, "", shape.what.format(name=name))
    if shape.pre is not None:
        shape.pre(d)
    check_keys(d, "", shape.what_keys.format(name=name), wire_keys(cls), shape.required)
    return d


@functools.cache
def _adapter(cls: Any) -> TypeAdapter:
    return TypeAdapter(cls)


def parse_model(cls: Any, data: Any, name: Any = None) -> Any:
    """Parse the JSON object ``data`` into a ``cls`` instance, strictly.

    Raises :class:`SpecParseError` carrying the first problem in ``cls``'s
    read order, with a pointer relative to ``data``; the fields after it are
    not read. ``name`` is the key the object is stored under, used in
    messages (``column 'revenue'``).
    """
    d = _check_wire_shape(cls, data, name)
    values = {key: at(pointer, parse, d[key])
              for key, pointer, parse in field_parsers(cls) if key in d}
    return build(cls, values)


def build(cls: Any, values: dict[str, Any]) -> Any:
    """``cls`` built by pydantic from already parsed field ``values`` (missing
    fields take their defaults; ``__post_init__`` runs). An exception from
    ``__post_init__`` is raised as is, or, if the class declares
    ``__post_init_issue__ = (code, pointer)`` and it is a plain
    ``ValueError``, as a :class:`SpecParseError` with that issue (a
    ``SpecError`` keeps its own issues)."""
    box: list[BaseException] = []
    token = POST_INIT_CATCH.set(box)
    try:
        return _adapter(cls).validate_python(values)
    except ValidationError as e:
        if not box:  # pydantic refused something itself; a bug in a class's wire shape
            raise SpecParseError([_fallback_issue(err) for err in e.errors()]) from None
    finally:
        POST_INIT_CATCH.reset(token)
    error = box[0]
    issue = getattr(cls, "__post_init_issue__", None)
    if issue is not None and isinstance(error, ValueError) and not isinstance(error, SpecError):
        code, pointer = issue
        raise SpecParseError([ValidationIssue(pointer, code, str(error))])
    raise error


def _fallback_issue(err: Any) -> ValidationIssue:
    loc = [str(p) for p in err.get("loc", ())]
    code = "missing_field" if err.get("type") == "missing" else "invalid_value"
    return ValidationIssue(ptr(*loc), code, str(err.get("msg", "")))


__all__ = [
    "SPEC_CONFIG", "Bool", "Int", "JsonObject", "Nested", "OptInt", "OptNumber", "OptStr",
    "SpecModel", "Str", "WireShape", "at", "build", "field_parsers", "model", "parse_array",
    "parse_model", "parse_object", "wire", "wire_keys",
]
