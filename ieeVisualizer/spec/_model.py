"""Pydantic plumbing shared by the model classes (see ``docs/MODELS.md``).

Every spec class is a :func:`pydantic.dataclasses.dataclass`. Its field
annotations declare the *wire type* of each field (what :func:`from_dict`
accepts for it) through the factories below (:func:`Str`, :func:`OptNumber`,
:func:`Nested`, :func:`wire`, ...), so a class's annotations are the one place its JSON shape
is written down, and pydantic drives parsing: it dispatches fields, tracks the
location of every error and builds the instances.

Two modes, chosen by the validation context:

* **construct** (``FigureSpec(...)``, ``Column(...)`` from Python; no context):
  the field validators pass every value through unchanged. Python
  construction stays exactly as permissive as before the migration - a value
  of the wrong type is stored and :func:`~ieeVisualizer.spec.validate`
  reports it - and the classes' ``__post_init__`` hooks still normalise
  (column values, canonical floats, encoding shorthand) and refuse NaN/inf.
* **parse** (:func:`parse_model`, used by ``from_dict``): every field validator
  checks the JSON value strictly - no coercion: ``"5"`` is not a number,
  ``true`` is not an integer, NaN/inf and numbers beyond float64 are refused,
  ``null`` only where the field is optional - and canonicalises it.

Errors map to the structured issues the library has always raised: a field
validator raises :class:`~ieeVisualizer.spec.SpecParseError` with a pointer
*relative to its field* (pydantic reports it as a ``value_error`` and keeps the
exception); :func:`issue_from_error` joins pydantic's ``loc`` with that
pointer into an RFC-6901 pointer from the parsed document's root and
keeps the issue code and message. Raw ``pydantic.ValidationError`` never
escapes: :func:`parse_model` raises ``SpecParseError``, and constructing a
:class:`SpecModel` re-raises the original exception of a ``__post_init__``
(or a ``TypeError`` for bad arguments, as stdlib dataclasses do).
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Annotated, Any, Callable, Optional

from pydantic import PlainValidator, TypeAdapter, ValidationError

from ._common import (
    SPEC_CONFIG,
    SpecError,
    SpecModel,
    SpecParseError,
    ValidationIssue,
    _jtype,
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

_PARSE = "parse"

#: pydantic error types that mean "called with the wrong arguments"; a
#: constructor raises ``TypeError`` for them, as stdlib dataclasses do.
_ARGUMENT_ERRORS = {
    "missing": "missing required argument",
    "missing_argument": "missing required argument",
    "missing_positional_only_argument": "missing required argument",
    "missing_keyword_only_argument": "missing required argument",
    "unexpected_keyword_argument": "got an unexpected keyword argument",
    "unexpected_positional_argument": "got too many positional arguments",
    "multiple_argument_values": "got multiple values for argument",
}


def _parsing(info: Any) -> bool:
    ctx = info.context
    return isinstance(ctx, dict) and ctx.get("mode") == _PARSE


# --------------------------------------------------------------------------
# Field types. ``wire(parse, base)`` is ``Annotated[base, <validator>]``:
# ``base`` documents the Python type; ``parse(value)`` runs in parse mode only
# and raises SpecParseError with paths relative to the field.
# --------------------------------------------------------------------------


def wire(parse: Callable[[Any], Any], base: Any = Any) -> Any:
    def validate(value: Any, info: Any) -> Any:
        return parse(value) if _parsing(info) else value

    return Annotated[base, PlainValidator(validate)]


def Str(what: str) -> Any:  # noqa: N802 - a type factory, used in annotations
    return wire(lambda v: expect_str(v, "", what), str)


def OptStr(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_str(v, "", what, optional=True), Optional[str])


def Bool(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_bool(v, "", what), bool)


def Int(what: str) -> Any:  # noqa: N802
    """An integer; an integer-valued float such as ``200.0`` reads as ``200``."""
    return wire(lambda v: expect_int(v, "", what), int)


def OptInt(what: str) -> Any:  # noqa: N802
    return wire(lambda v: expect_int(v, "", what, optional=True), Optional[int])


def OptNumber(what: str) -> Any:  # noqa: N802
    """A finite number stored as a canonical float, or null."""
    return wire(lambda v: expect_number(v, "", what, optional=True), Optional[float])


def JsonObject(what: str) -> Any:  # noqa: N802
    """Free-form JSON object (style, metadata, provenance extra), deep-copied."""
    return wire(lambda v: copy_json(expect_dict(v, "", what), "", what), dict[str, Any])


def Nested(cls: Any) -> Any:  # noqa: N802
    """A nested model object, parsed by its own class (see :func:`parse_model`)."""
    return wire(lambda v: parse_model(cls, v), cls)


def at(pointer: str, parse: Callable[..., Any], *args: Any) -> Any:
    """``parse(*args)``, with ``pointer`` prepended to the path of any
    :class:`SpecParseError` it raises (paths are relative to what is parsed)."""
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
    mark's, which its class fixes); ``order`` is the field order in which the
    first error of an object is reported (default: declaration order); ``pre``
    runs on the raw dict before the key check (``schema_version``).
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


def _check_wire_shape(cls: Any, data: Any, name: Any) -> dict[str, Any]:
    shape: WireShape = cls.__wire__
    d = expect_dict(data, "", shape.what.format(name=name))
    if shape.pre is not None:
        shape.pre(d)
    check_keys(d, "", shape.what_keys.format(name=name), wire_keys(cls), shape.required)
    return {k: v for k, v in d.items() if k not in shape.drop}


@functools.cache
def _adapter(cls: Any) -> TypeAdapter:
    return TypeAdapter(cls)


def parse_model(cls: Any, data: Any, name: Any = None) -> Any:
    """Parse the JSON object ``data`` into a ``cls`` instance, strictly.

    Raises :class:`SpecParseError` carrying the first problem in ``cls``'s
    field order, with a pointer relative to ``data``. ``name`` is the key the
    object is stored under, used in messages (``column 'revenue'``).
    """
    d = _check_wire_shape(cls, data, name)
    try:
        return _adapter(cls).validate_python(d, context={"mode": _PARSE})
    except ValidationError as e:
        raise SpecParseError([first_issue(e, cls)]) from None


# --------------------------------------------------------------------------
# Error mapping: pydantic errors -> ValidationIssue (path, code, message).
# --------------------------------------------------------------------------


def first_issue(exc: ValidationError, cls: Any) -> ValidationIssue:
    """The issue for the first error of ``exc``, in ``cls``'s parse order."""
    order = list(getattr(getattr(cls, "__wire__", None), "order", ()))

    def rank(err: dict[str, Any]) -> int:
        loc = err["loc"]
        return order.index(loc[0]) if loc and loc[0] in order else len(order)

    errors = sorted(exc.errors(include_url=False), key=rank)  # stable
    return issue_from_error(errors[0], cls)


def issues_from_validation_error(exc: ValidationError, cls: Any = None) -> list[ValidationIssue]:
    """Every error of ``exc`` as a :class:`ValidationIssue`."""
    return [issue_from_error(e, cls) for e in exc.errors(include_url=False)]


def issue_from_error(err: dict[str, Any], cls: Any = None) -> ValidationIssue:
    """Map one pydantic error (a dict of ``ValidationError.errors()``).

    * a :class:`SpecError` raised by a field validator (pydantic reports it
      as ``value_error`` and keeps the exception in ``ctx["error"]``): its
      first issue, with its pointer appended to the field's location;
    * any other exception from a ``__post_init__``: the class's
      ``__post_init_issue__`` code and pointer (``invalid_value`` at the
      object by default), the exception text as message;
    * missing / unexpected arguments: ``missing_field`` / ``unknown_field``;
    * anything else (a declared pydantic type refusing a value):
      ``invalid_type`` at the location, with pydantic's reason in the message.
    """
    loc = _named_loc(tuple(err.get("loc", ())), cls)
    base = ptr(*loc)
    error = (err.get("ctx") or {}).get("error")
    if isinstance(error, SpecError) and error.issues:
        i = error.issues[0]
        return ValidationIssue(base + i.path, i.code, i.message, i.severity)
    if isinstance(error, BaseException):
        code, pointer = getattr(cls, "__post_init_issue__", ("invalid_value", ""))
        return ValidationIssue(base + pointer, code, str(error))
    kind = err.get("type", "")
    what = f"a {getattr(cls, '__name__', 'value')}"
    field = str(loc[-1]) if loc else "value"
    if kind in ("missing", "missing_argument"):
        return ValidationIssue(base, "missing_field", f"{what} requires the field {field!r}.")
    if kind in ("extra_forbidden", "unexpected_keyword_argument"):
        return ValidationIssue(base, "unknown_field", f"{what} has no field {field!r}.")
    expected = _EXPECTED.get(kind)
    if expected is None:  # pydantic's own sentence, e.g. for a literal
        expected = str(err.get("msg", kind)).rstrip(".")
        expected = expected[len("Input should be "):] if expected.startswith("Input should be ") \
            else expected
    value = err.get("input")
    return ValidationIssue(base, "invalid_type",
                           f"{field} must be {expected}, got {_jtype(value)} {short(value)}.")


#: What a pydantic type error asked for, in the library's words.
_EXPECTED = {
    "string_type": "a string", "int_type": "an integer", "float_type": "a number",
    "bool_type": "true or false", "bytes_type": "bytes", "list_type": "a list",
    "tuple_type": "a tuple", "dict_type": "an object (dict)", "frozen_set_type": "a frozenset",
    "set_type": "a set", "dataclass_exact_type": "an instance of the declared class",
    "is_instance_of": "an instance of the declared class",
}


def short(v: Any) -> str:
    """``repr(v)``, shortened for a message."""
    try:
        s = repr(v)
    except Exception:  # noqa: BLE001 - a broken __repr__ must not hide the real error
        return f"<{type(v).__name__}>"
    return s if len(s) <= 60 else s[:57] + "..."


def _named_loc(loc: tuple, cls: Any) -> tuple:
    """pydantic locates a positional constructor argument by its index; use
    the field name instead, so the pointer names the field."""
    if loc and isinstance(loc[0], int) and dataclasses.is_dataclass(cls):
        names = [f.name for f in dataclasses.fields(cls) if f.init]
        if loc[0] < len(names):
            return (names[loc[0]],) + loc[1:]
    return loc


def construction_error(exc: ValidationError, cls: Any) -> Exception:
    """The exception a Python constructor raises for ``exc``: the original
    exception of a ``__post_init__`` or field validator, ``TypeError`` for
    bad arguments, else :class:`SpecParseError` with structured issues."""
    errors = exc.errors(include_url=False)
    first = errors[0]
    error = (first.get("ctx") or {}).get("error")
    if isinstance(error, Exception):
        return error
    if first.get("type") in _ARGUMENT_ERRORS:
        loc = _named_loc(tuple(first.get("loc", ())), cls)
        name = loc[-1] if loc else "?"
        return TypeError(f"{cls.__name__}.__init__() {_ARGUMENT_ERRORS[first['type']]} "
                         f"{name!r}")
    return SpecParseError(issues_from_validation_error(exc, cls))


__all__ = [
    "SPEC_CONFIG", "Bool", "Int", "JsonObject", "Nested", "OptInt", "OptNumber", "OptStr",
    "SpecModel", "Str", "WireShape", "at", "construction_error", "first_issue",
    "issue_from_error", "issues_from_validation_error", "model", "parse_array", "parse_model",
    "parse_object", "wire", "wire_keys",
]
