"""Shared primitives for the FigureSpec data model.

Nothing in this package may import matplotlib (or any renderer): the spec is a
pure, JSON-serializable description of a figure. Branch-3 renderer adapters
consume it; they are not imported by it.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import hashlib
import inspect
import json
import math
import numbers
import sys
from abc import ABCMeta
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as pydantic_dataclass

#: The schema version written by this library. Bump on any change that alters
#: the meaning of an existing serialized spec (historical report figures must
#: not silently change meaning).
SCHEMA_VERSION = "1.0"

#: Versions :func:`from_dict` can read. Anything else is refused loudly.
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("1.0",)

SEVERITIES = ("error", "warning", "info")

# --------------------------------------------------------------------------
# Model classes are pydantic dataclasses (see ``_model`` and docs/MODELS.md).
# --------------------------------------------------------------------------

#: Configuration of the model classes. Nothing is coerced or checked by
#: pydantic itself: a spec class's fields are ``Annotated[Any, Wire(...)]``
#: (``_model``), read from JSON by their own parsers in the old parser's order,
#: and a descriptor class is only ever built by its constructor. Unknown keys
#: are refused by ``_model.parse_model`` (with a did-you-mean) before pydantic
#: sees them. ``defer_build`` keeps ``import ieeVisualizer.spec`` cheap: a
#: class's validator is built on first use.
SPEC_CONFIG = ConfigDict(
    extra="forbid", arbitrary_types_allowed=True, revalidate_instances="never", defer_build=True,
)


class Wire:
    """Field-annotation metadata of a spec class: how the field is read from
    JSON (``parse(value)``, raising :class:`SpecParseError` with paths
    relative to the field) and its Python type as the stdlib dataclass
    showed it in signatures (``text``, e.g. ``"str | None"``)."""

    __slots__ = ("parse", "text")

    def __init__(self, parse: Any, text: str) -> None:
        self.parse, self.text = parse, text

    def __repr__(self) -> str:
        return f"Wire({self.text})"


def _type_text(t: Any) -> str:
    """``t`` written the way it was in the source before the migration
    (``Optional[str]`` -> ``"str | None"``), for signatures and ``__doc__``."""
    if isinstance(t, str):
        return t
    if t is Any:
        return "Any"
    if t is type(None) or t is None:
        return "None"
    if t is Ellipsis:
        return "..."
    origin, args = get_origin(t), get_args(t)
    if origin is Annotated:
        wire = [m for m in getattr(t, "__metadata__", ()) if isinstance(m, Wire)]
        return wire[0].text if wire else _type_text(args[0])
    if origin is Union:
        return " | ".join(_type_text(a) for a in args)
    if origin is not None:
        return f"{getattr(origin, '__name__', origin)}[{', '.join(map(_type_text, args))}]"
    return getattr(t, "__name__", repr(t))


def _field_text(cls: Any, f: dataclasses.Field) -> str:
    if not isinstance(f.type, str):
        return _type_text(f.type)
    module = sys.modules.get(cls.__module__)
    try:
        return _type_text(eval(f.type, vars(module) if module else {}))  # noqa: S307
    except Exception:  # noqa: BLE001 - an annotation that cannot be evaluated is shown as written
        return f.type


#: Set by ``_model`` while pydantic builds a parsed object: the box that
#: receives the exception its ``__post_init__`` raises (see :func:`_guarded`).
POST_INIT_CATCH: ContextVar[list | None] = ContextVar("POST_INIT_CATCH", default=None)


class PostInitFailed(ValueError):
    """Stands in, inside pydantic, for the exception a ``__post_init__``
    raised while a parsed object was built; ``_model`` re-raises the original
    (pydantic would otherwise render its text, which fails for a lone
    surrogate, and wrap it)."""


def _guarded(post_init: Any) -> Any:
    @functools.wraps(post_init)
    def __post_init__(self: Any) -> None:  # noqa: N807
        box = POST_INIT_CATCH.get()
        if box is None:  # a Python constructor (or a constructor inside one): unchanged
            return post_init(self)
        POST_INIT_CATCH.set(None)  # objects this hook constructs run unguarded
        try:
            return post_init(self)
        except Exception as e:
            box.append(e)
            raise PostInitFailed("__post_init__ failed") from None

    return __post_init__


class _ModelMeta(ABCMeta):
    """Keeps the stdlib dataclass ``__init__`` of the model classes.

    ``pydantic.dataclasses.dataclass`` first lets the stdlib generate
    ``__init__`` and then replaces it (when decorating, and again when it
    builds a deferred class) with ``__init__(self, *args, **kwargs)`` that
    runs its validator, and sets ``__signature__`` to match. The model
    classes construct exactly as the stdlib dataclasses did before the
    migration (same binding of positional arguments, ``TypeError`` texts,
    stored objects, ``__post_init__`` exceptions and cost; pydantic's own
    ``__init__`` would also count ``init=False`` fields such as a mark's
    ``type`` as positional parameters), so those two assignments, when
    pydantic's own code makes them, are ignored. pydantic builds objects only
    when parsing JSON (``_model.parse_model``), which does not go through
    ``__init__``.

    It derives from ``ABCMeta`` so a model class can be mixed with an
    ``abc.ABC`` base."""

    def __setattr__(cls, name: str, value: Any) -> None:
        if name in ("__init__", "__signature__") and \
                str(sys._getframe(1).f_globals.get("__name__")).startswith("pydantic."):
            return  # assigned by pydantic's own code
        super().__setattr__(name, value)


class SpecModel(metaclass=_ModelMeta):
    """Base class of every model class (decorate subclasses with :func:`model`)."""


def model(cls: Any = None, *, frozen: bool = False, config: ConfigDict = SPEC_CONFIG) -> Any:
    """``pydantic.dataclasses.dataclass`` with the library's configuration, for
    a :class:`SpecModel` subclass, keeping what the stdlib dataclass had:
    its ``__init__`` (see :class:`_ModelMeta`), the field types in
    ``inspect.signature`` as they were written before the migration, and the
    generated ``__doc__`` of a class without a docstring."""

    def wrap(cls: Any) -> Any:
        if not issubclass(cls, SpecModel):
            raise TypeError(f"{cls.__name__} must subclass SpecModel.")
        doc = cls.__dict__.get("__doc__")
        post_init = cls.__dict__.get("__post_init__")
        if post_init is not None:
            type.__setattr__(cls, "__post_init__", _guarded(post_init))
        cls = pydantic_dataclass(cls, config=config, frozen=frozen)
        init = cls.__dict__["__init__"]
        init.__annotations__ = {**{f.name: _field_text(cls, f) for f in dataclasses.fields(cls)
                                   if f.init}, "return": None}
        if doc is None:
            text = str(inspect.signature(cls)).replace(" -> None", "")
            type.__setattr__(cls, "__doc__", cls.__name__ + text)
        return cls

    return wrap if cls is None else wrap(cls)


@model(frozen=True)
class ValidationIssue(SpecModel):
    """One structured finding about a spec.

    ``path`` is an RFC-6901 JSON pointer into the *serialized* spec (the same
    pointer an agent would pass to :func:`apply_patch` to fix it), ``code`` is
    a stable machine-readable identifier, ``message`` is a readable sentence
    that names the fix, and ``severity`` is ``"error"``, ``"warning"`` or
    ``"info"``. Only errors make a spec unrenderable.
    """

    path: str
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


class SpecError(ValueError):
    """Base class for spec failures. Carries the structured ``issues``."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues: list[ValidationIssue] = list(issues)
        super().__init__(
            "; ".join(f"{i.path or '/'}: [{i.code}] {i.message}" for i in self.issues)
        )


class SpecParseError(SpecError):
    """The input cannot be represented as a FigureSpec at all (wrong JSON
    types, unknown keys, unknown mark type, unsupported schema_version).
    Parsing stops at the first such problem."""


class SpecValidationError(SpecError):
    """The spec parsed but :func:`validate` reported errors."""


class PatchError(SpecError):
    """A patch operation could not be applied (bad pointer, bad op)."""


def ptr(*parts: Any) -> str:
    """Build a JSON pointer from raw path parts, escaping ``~`` and ``/``."""
    out = []
    for p in parts:
        s = str(p).replace("~", "~0").replace("/", "~1")
        out.append(s)
    return "/" + "/".join(out) if out else ""


def canonical_json(obj: Any, indent: int | None = None) -> str:
    """Canonical JSON: sorted keys, no NaN/Infinity, UTF-8 preserved.

    With ``indent=None`` the separators are compact, so the output is a stable
    byte sequence suitable for hashing.
    """
    seps = (",", ":") if indent is None else (",", ": ")
    return json.dumps(
        obj, sort_keys=True, separators=seps, ensure_ascii=False, allow_nan=False, indent=indent
    )


def sha256_of(obj: Any) -> str:
    """``"sha256:<hex>"`` digest of the canonical JSON of ``obj``."""
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Small structural-parsing helpers used by every from_dict.
# --------------------------------------------------------------------------


def fail(path: str, code: str, message: str) -> SpecParseError:
    return SpecParseError([ValidationIssue(path, code, message, "error")])


def expect_dict(value: Any, path: str, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise fail(path, "invalid_type", f"{what} must be a JSON object, got {_jtype(value)}.")
    for k in value:
        if not isinstance(k, str):
            raise fail(path, "invalid_type", f"{what} keys must be strings, got {k!r}.")
    return value


def expect_list(value: Any, path: str, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise fail(path, "invalid_type", f"{what} must be a JSON array, got {_jtype(value)}.")
    return value


def expect_str(value: Any, path: str, what: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise fail(path, "invalid_type", f"{what} must be a string, got {_jtype(value)}.")
    return value


def expect_number(value: Any, path: str, what: str, optional: bool = False) -> float | None:
    """A finite JSON number, returned in canonical float form (see :func:`canon_float`)."""
    if value is None and optional:
        return None
    if not is_number(value):
        raise fail(path, "invalid_type", f"{what} must be a number, got {_jtype(value)}.")
    if beyond_float64(value):
        raise out_of_range_error(path, what, value)
    if not math.isfinite(value):
        raise non_finite_error(path, what, value)
    return canon_float(value, path, what)


def expect_int(value: Any, path: str, what: str, optional: bool = False) -> int | None:
    """An integer; an integer-valued float such as ``200.0`` is accepted and
    returned as ``200`` (JSON does not distinguish them, nor does the schema)."""
    if value is None and optional:
        return None
    if not is_number(value):
        raise fail(path, "invalid_type", f"{what} must be an integer, got {_jtype(value)}.")
    if beyond_float64(value):
        raise out_of_range_error(path, what, value)
    if not math.isfinite(value):
        raise non_finite_error(path, what, value)
    out = canon_int(value, path, what)
    if not isinstance(out, int):
        raise fail(path, "invalid_type", f"{what} must be an integer, got {value!r}.")
    return out


def non_finite_error(path: str, what: str, value: Any) -> SpecParseError:
    return fail(path, "non_finite_number",
                f"{what} is {value!r}; NaN and infinities are not valid JSON numbers. Use a "
                "finite number, or null where the field allows it.")


def beyond_float64(v: Any) -> bool:
    """An exact number (e.g. a Python int such as ``10**400``) too large for a
    float64. JSON itself has no limit, but no reader that parses numbers as
    doubles (JavaScript, numpy, the renderer) can hold it."""
    if not is_number(v):
        return False
    try:
        float(v)
    except OverflowError:
        return True
    return False


def out_of_range_error(path: str, what: str, value: Any) -> SpecParseError:
    """``number_out_of_range`` for a value :func:`beyond_float64` refuses."""
    return fail(path, "number_out_of_range",
                f"{what} {short_number(value)} is beyond the float64 range (about +-1.8e308), "
                "so it cannot be stored as a number that readers can hold; rescale it (e.g. "
                "change the unit) upstream.")


def short_number(v: Any) -> str:
    """``str(v)``, shortened for a message (and safe beyond Python's int ->
    str digit limit)."""
    try:
        s = str(v)
    except ValueError:  # beyond Python's int -> str digit limit
        return "(a very large integer)"
    return s if len(s) <= 24 else f"{s[:12]}... ({len(s)} digits)"


# --------------------------------------------------------------------------
# Numeric canonicalization. Fields declared as numbers are stored as Python
# floats (-0.0 as 0.0) and fields declared as integers as ints, so that equal
# specs serialize to identical bytes: 5 and 5.0 are the same JSON number, and
# a JavaScript JSON.parse/JSON.stringify round trip writes 5.0 back as 5.
# --------------------------------------------------------------------------


def is_number(v: Any) -> bool:
    """A real number that is not a bool (numpy scalars included)."""
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and not _is_np_bool(v)


def _is_np_bool(v: Any) -> bool:
    return type(v).__name__ in ("bool_", "bool") and type(v).__module__ == "numpy"


def canon_float(v: Any, path: str = "", what: str = "value") -> Any:
    """Canonical float for a declared-numeric field; other values are returned
    unchanged (the validator reports them). Raises ``ValueError`` for NaN/inf,
    which no JSON document can hold, and :class:`SpecParseError`
    (``number_out_of_range`` at ``path``, also a ``ValueError``) for a number
    beyond the float64 range, such as ``10**400``."""
    if not is_number(v):
        return v
    if beyond_float64(v):
        raise out_of_range_error(path, what, v)
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{v!r} is not a finite number; NaN and infinities cannot be stored.")
    return f + 0.0  # -0.0 + 0.0 == +0.0


def canon_int(v: Any, path: str = "", what: str = "value") -> Any:
    """Canonical int for a declared-integer field: integral numbers (``3``,
    ``3.0``, ``numpy.int64(3)``) become ``int``; anything else is unchanged.
    Raises :class:`SpecParseError` (``number_out_of_range``) as
    :func:`canon_float` does."""
    if beyond_float64(v):
        raise out_of_range_error(path, what, v)
    if is_number(v) and math.isfinite(v) and float(v).is_integer():
        return int(v)
    return v


#: Deepest nesting accepted in free-form JSON (``metadata``, ``style``,
#: ``provenance.extra``); the object itself is depth 1. Deeper values are
#: refused with ``too_deep`` instead of reaching recursive code (JSON
#: encoding, deepcopy) that would raise RecursionError.
MAX_JSON_DEPTH = 64
#: Deepest nesting :func:`copy_tree` copies: generous enough for any document
#: whose free-form parts respect ``MAX_JSON_DEPTH``, well below Python's
#: recursion limit, so the copy can always be JSON-encoded afterwards.
MAX_TREE_DEPTH = 256


def _too_deep(path: str, what: str, limit: int) -> SpecParseError:
    return fail(path, "too_deep", f"{what} is nested more than {limit} levels deep here; "
                "flatten it.")


def copy_json(value: Any, path: str, what: str) -> Any:
    """Validate free-form JSON (metadata, style, provenance.extra) and return a
    deep copy, so a parsed spec never aliases its input. Rejects NaN/inf,
    non-JSON types and nesting deeper than ``MAX_JSON_DEPTH`` with the
    pointer of the offending value (the first one in document order).
    Iterative: no input can make it raise RecursionError."""
    box: list[Any] = [None]
    todo: list[tuple[Any, str, int, Any, Any]] = [(value, path, 1, box, 0)]
    while todo:
        v, p, depth, parent, slot = todo.pop()
        if isinstance(v, (dict, list)) and depth > MAX_JSON_DEPTH:
            raise _too_deep(p, what, MAX_JSON_DEPTH)
        if isinstance(v, dict):
            out: Any = {}
            kids = []
            for k, x in v.items():
                if not isinstance(k, str):
                    raise fail(p, "invalid_type", f"{what} keys must be strings, got {k!r}.")
                out[k] = None
                kids.append((x, ptr_join(p, k), depth + 1, out, k))
            todo.extend(reversed(kids))
        elif isinstance(v, list):
            out = [None] * len(v)
            todo.extend(reversed([(x, ptr_join(p, i), depth + 1, out, i)
                                  for i, x in enumerate(v)]))
        elif v is None or isinstance(v, (str, bool)):
            out = v
        elif isinstance(v, (int, float)):
            if isinstance(v, float) and not math.isfinite(v):
                raise non_finite_error(p, what, v)
            out = v
        else:
            raise fail(p, "invalid_type", f"{what} must hold only JSON values, got {_jtype(v)}.")
        parent[slot] = out
    return box[0]


def copy_tree(value: Any, path: str = "", what: str = "the document",
              limit: int = MAX_TREE_DEPTH) -> Any:
    """Deep copy of a dict/list/tuple tree (other values are deep-copied as
    leaves), iterative, refusing nesting deeper than ``limit`` (or a reference
    cycle) with ``too_deep``. Use instead of ``copy.deepcopy`` on anything a
    caller built.

    Like ``deepcopy`` it keeps a memo by ``id()``: an object referenced from
    several places is copied once and stays shared in the copy, so ``[a, a]``
    nested ``n`` deep costs ``O(n)``, not ``O(2**n)``. A reference reached
    again is still checked against ``limit`` at its new depth (by the height
    of its subtree). A ``namedtuple`` is rebuilt as its own type; any other
    tuple subclass (``time.struct_time``, ``os.stat_result``, ...), which
    cannot be rebuilt from its items, is copied as a list, which is what a
    tuple is in JSON."""
    box: list[Any] = [None]
    done: dict[int, Any] = {}  # id -> finished copy
    height: dict[int, int] = {}  # id -> container levels from it down (itself = 1)
    open_: set[int] = set()  # ids of the containers being copied (the current path)
    leaf_memo: dict[int, Any] = {}  # deepcopy's own memo for leaves
    # ("enter", value, pointer, depth, parent, slot) or ("exit", value, out, parent, slot)
    todo: list[tuple[Any, ...]] = [("enter", value, path, 1, box, 0)]
    while todo:
        item = todo.pop()
        if item[0] == "exit":
            _, v, out, parent, slot = item
            kids = v.values() if isinstance(v, dict) else v
            height[id(v)] = 1 + max((height[id(x)] for x in kids
                                     if isinstance(x, (dict, list, tuple))), default=0)
            if isinstance(v, tuple):
                kind = type(v)
                if kind is tuple:
                    out = tuple(out)
                elif hasattr(kind, "_fields") and hasattr(kind, "_make"):
                    out = kind._make(out)  # a namedtuple
                # any other tuple subclass stays the list it was filled into
                parent[slot] = out
            open_.discard(id(v))
            done[id(v)] = out
            continue
        _, v, p, depth, parent, slot = item
        if isinstance(v, (dict, list, tuple)):
            key = id(v)
            if key in open_:
                raise fail(p, "too_deep", f"{what} contains itself here (a reference cycle), "
                           "so it is nested infinitely deep; JSON cannot hold it.")
            if depth > limit or (key in done and depth + height[key] - 1 > limit):
                raise _too_deep(p, what, limit)
            if key in done:
                parent[slot] = done[key]
                continue
            open_.add(key)
            if isinstance(v, dict):
                out: Any = dict.fromkeys(v)
                kids = [("enter", x, ptr_join(p, k), depth + 1, out, k) for k, x in v.items()]
            else:
                out = [None] * len(v)
                kids = [("enter", x, ptr_join(p, i), depth + 1, out, i)
                        for i, x in enumerate(v)]
            todo.append(("exit", v, out, parent, slot))
            todo.extend(reversed(kids))  # document order: the first child is popped first
        elif v is None or isinstance(v, (str, bool, int, float)):
            out = v
        else:
            out = copy.deepcopy(v, leaf_memo)
        parent[slot] = out
    return box[0]


def json_problem(value: Any, path: str) -> tuple[str, str, str] | None:
    """First non-JSON or non-finite value inside ``value`` as ``(path, code,
    message)``, or None. Non-raising counterpart of :func:`copy_json`."""
    try:
        copy_json(value, path, "free-form JSON")
    except SpecParseError as e:
        i = e.issues[0]
        return i.path, i.code, i.message
    return None


def expect_bool(value: Any, path: str, what: str) -> bool:
    if not isinstance(value, bool):
        raise fail(path, "invalid_type", f"{what} must be true or false, got {_jtype(value)}.")
    return value


def check_keys(
    d: dict[str, Any], path: str, what: str, allowed: Iterable[str], required: Iterable[str] = ()
) -> None:
    allowed = set(allowed)
    for k in required:
        if k not in d:
            raise fail(ptr_join(path, k), "missing_field", f"{what} requires the field {k!r}.")
    unknown = sorted(set(d) - allowed)
    if unknown:
        k = unknown[0]
        hint = suggest(k, allowed)
        raise fail(
            ptr_join(path, k),
            "unknown_field",
            f"{what} has no field {k!r}.{hint} Allowed fields: {', '.join(sorted(allowed))}.",
        )


def ptr_join(base: str, *parts: Any) -> str:
    return base + ptr(*parts)


def suggest(name: str, choices: Iterable[str]) -> str:
    import difflib

    close = difflib.get_close_matches(name, sorted(choices), n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


def _jtype(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__
