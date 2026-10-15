# Model classes (pydantic)

The spec classes (`FigureSpec`, marks, datasets, axes, legend, annotations,
provenance), `ValidationIssue`, the render contracts (`RenderRequest`,
`RenderResult`, `RenderWarning`, `Capabilities`), the artifact manifest
(`VisualizationArtifact`, `FileEntry`, `RenderVariant`, `DatasetRecord`) and the
template descriptors (`Parameter`, `ColumnRequirement`) are
[`pydantic.dataclasses`](https://docs.pydantic.dev/latest/concepts/dataclasses/)
(pydantic v2). This note says why, how strictness is configured, how pydantic
errors become the library's structured issues, and how to write a new model.

## Why

Before this change every class had two hand-maintained descriptions: the
dataclass fields and a procedural `*_from_dict` parser that re-listed every key,
its type and its error message (about 200 lines across `data.py`, `marks.py`
and `figure.py`). Templates and render requests declared their fields a third way.
Now a field's annotation *is* its wire type:

```python
@model
class Axis(SpecModel):
    __wire__ = WireShape("an axis", order=("limits", "label", "unit", "scale"))

    label: OptStr("label") = None
    unit: OptStr("unit") = None
    scale: OptStr("scale") = None
    limits: wire(_parse_limits, Optional[list[Optional[Scalar]]]) = None
```

and pydantic does the rest of parsing: it dispatches each key to its field,
tracks where every error happened, builds the instances and runs
`__post_init__`. Descriptor classes (`Parameter`, `RenderRequest`, ...) are
checked by pydantic on construction, so a template that declares
`Parameter("n", "integr", ...)` or `ColumnRequirement(dtypes=("money",))`, or a
manifest whose `size_bytes` is `"12"`, fails where it is written instead of
much later.

What did **not** change, and is pinned by tests:

* the JSON wire format, the canonical bytes of `to_json`, every `spec_hash` and
  `content_hash`, and the meaning of `schema_version` 1.0
  (`tests/spec_hash_baseline.json`, recorded from `main` before the migration;
  see [Verification](#verification));
* every public constructor (positional and keyword arguments, encoding
  shorthand, numpy arrays into `Column`, mutability: spec classes are mutable,
  `ValidationIssue`/render contracts/template descriptors are frozen);
* the error contract: parse errors are `SpecParseError` (a `ValueError`) with
  RFC-6901 pointers and the same issue codes; `validate()` never raises and
  returns the same codes and paths;
* `json_schema()` (still hand-written, see [JSON schema](#json-schema)).

Serialization (`to_dict`/`to_json`) stays explicit code, not pydantic's
serializer: the bytes are the contract (omitted `None`s, explicit defaults,
canonical floats and datetimes, re-normalised column values) and a generic
serializer would have to be configured into exactly that shape anyway.

## Two modes: construct and parse

The spec classes deliberately differ from a typical pydantic model:

* **Construct** (`FigureSpec(...)`, `Column(...)` from Python). Every field
  validator passes the value through unchanged, so Python construction is as
  permissive as before: `DatasetRef(kind=5, id="x")` or
  `HistogramMark(bins=2.5)` constructs, and `validate()` reports the problem
  with a pointer. Containers are not copied (`FigureSpec(layers=xs).layers is
  xs`). The `__post_init__` hooks still normalise (column values, canonical
  floats, encoding shorthand, time coordinates) and refuse NaN/inf exactly as
  before. This is what lets an agent build a spec, `validate()` it and patch
  it, instead of getting an exception per mistake.
* **Parse** (`from_dict`, `from_json`, `apply_patch`, template inputs). The
  same validators run strictly; see below.

The mode is the pydantic validation context: `parse_model()` passes
`{"mode": "parse"}`; a constructor call has no context.

`validate()` is unchanged: its type layer (`_t_str`, `_t_number`, ...) still
checks objects built or mutated in Python, with its own messages, and its
semantic checks are untouched. Driving that type layer from the same
annotations is the natural next step (see [Gaps](#gaps-and-follow-ups)).

## How strictness is configured

Spec classes use `SPEC_CONFIG` (`spec/_common.py`):

```python
ConfigDict(extra="forbid", arbitrary_types_allowed=True,
           revalidate_instances="never", defer_build=True)
```

pydantic's own `strict=True` is **not** used for them, because each field
carries a parse validator (`spec/_model.py`) that is stricter than pydantic's
strict mode and speaks the library's error codes:

| factory | accepts in parse mode | refuses |
|---|---|---|
| `Str(what)` / `OptStr(what)` | a string (or `null` for `OptStr`) | numbers, bools, `null` where not optional |
| `Bool(what)` | `true` / `false` | `1`, `"true"` |
| `Int(what)` / `OptInt(what)` | an integer; an integer-valued float (`200.0`) reads as `200` (JSON does not distinguish them) | `true`, `"5"`, `5.5`, NaN/inf, beyond float64 |
| `OptNumber(what)` | a finite number, stored as a canonical float (`-0.0` → `0.0`) | bools, strings, NaN/inf (`non_finite_number`), beyond float64 (`number_out_of_range`) |
| `JsonObject(what)` | a JSON object of JSON values, deep-copied | non-JSON values, NaN/inf anywhere inside |
| `Nested(cls)` | an object, parsed by `cls` | |
| `wire(parse, type)` | whatever `parse(value)` accepts | whatever it raises |

`null` stays `null` (never `0` or `""`), unknown keys are refused with a
did-you-mean (`check_keys`, run by `parse_model` before pydantic sees the
dict), and datetimes are canonicalised by the same code as before
(`Column.__post_init__`, `canonicalize_time_coordinates`).

Descriptor classes (`ValidationIssue`, render contracts, manifest, template
descriptors) use `STRICT_CONFIG` = pydantic strict mode
(`strict=True, extra="forbid"`): their fields are checked on construction with
no coercion. Where pydantic's strict types would *change a value*, the field
uses a validator instead: strict `float` turns `11` into `11.0`, which would
change `RenderRequest.to_dict()` and so the manifest bytes, so `width`,
`height` and `dpi` use a "positive finite number, kept as given" validator.

`defer_build=True` builds a class's pydantic validator on first use, which keeps
`import ieeVisualizer.spec` from building ~20 schemas up front.

## How errors map to issues

A parse validator raises `SpecParseError` with a pointer *relative to its
field* (it reuses the `expect_*` helpers in `_common.py`, called with path
`""`). pydantic records it as a `value_error` at the field's `loc` and keeps
the exception in `ctx["error"]`. `_model.issue_from_error` turns one pydantic
error into a `ValidationIssue`:

| pydantic error | issue |
|---|---|
| `value_error` carrying a `SpecError` | its code and message; path = pointer of `loc` + the error's relative pointer |
| `value_error` from `__post_init__` (any other exception) | the class's `__post_init_issue__` (e.g. `Column`: `invalid_values` at `/values`), else `invalid_value` at the object; the exception text is the message |
| `missing` / `missing_argument` | `missing_field` |
| `extra_forbidden` / `unexpected_keyword_argument` | `unknown_field` |
| any other type error (`string_type`, `int_type`, `tuple_type`, ...) | `invalid_type`: `"<field> must be a string, got number 5."` |

Pointers are built with `ptr()`, so `~` and `/` in keys are escaped
(`/datasets/a~1b`). A positional constructor argument (pydantic's `loc` is its
index) is reported by field name.

Nested objects compose: a list or dict field parses each item through
`parse_object` / `parse_array`, which prepend the item's pointer (`at()`), and a
nested model is parsed by its own `parse_model` call. So the issue for a bad
column unit is `/datasets/d/columns/y/unit` however deep it sits.

**First problem only.** `SpecParseError` from `from_dict` carries one issue, as
before. pydantic collects every field error of an object; `first_issue` reports
the first in the class's parse order (`WireShape.order`, which matches the old
parser: e.g. a `FigureSpec` reads `id`, `title`, `caption`, `theme`,
`coordinate_system`, `datasets`, `layers`, ... and an axis reads `limits`
first), and container items are read in document order.

**No raw pydantic error escapes.** `parse_model` raises `SpecParseError`.
Constructors go through the `SpecModel` metaclass, which catches
`ValidationError` and raises instead (`construction_error`):

* the original exception, when a `__post_init__` or field validator raised one
  (`Column([1, "a"])` is still `ValueError("column mixes value types ...")`,
  `Axis(limits=[0, 10**400])` still `SpecParseError` at `/limits/1`);
* `TypeError` for wrong arguments (`Encoding()`, `LineMark(type="bar")`), as a
  stdlib dataclass raises;
* otherwise `SpecParseError` with every issue, e.g.
  `RenderRequest(theme=5)` → `/theme: [invalid_type] theme must be a string,
  got number 5.`

The hook is on the metaclass, not `__init__`, because pydantic replaces
`__init__` when it builds a deferred class.

`tests/spec/test_spec_models.py` pins the mapping: one test per issue code
with its pointer, no coercion, parse order, constructor exceptions,
descriptor type errors. The parse-path cases there also pass against the
pre-migration parser (checked on a scratch copy of `main`), which is the
evidence that codes and paths are unchanged.

## JSON schema

`json_schema()` is still the hand-written draft-07 schema. pydantic's
generated schema (`TypeAdapter(FigureSpec).json_schema()`) diverges: the parse
validators are `PlainValidator`s, so pydantic sees untyped fields, and the wire
details (the `type` discriminators of datasets, marks and annotations, encoding
shorthand, enums, the ISO-8601 pattern, `minItems`/`maxItems`) live in the
parsers and the hand-written schema, not in annotations. Instead of generating
it, `test_model_wire_keys_match_the_json_schema` checks that every model's
wire keys and required keys equal the schema's `properties` and `required`, so
the two cannot drift apart; the existing schema ↔ parser tests still pass.

## Import cost

pydantic is imported by `import ieeVisualizer.spec` (matplotlib and sympy
still are not; `tests/spec/test_spec_import.py` checks it). Measured with
`tools/import_time.py` (25 fresh interpreters each, on a shared and noisy
machine, so the minimum is the steadier figure):

| `import ieeVisualizer.spec` | before (min / median) | after (min / median) |
|---|---|---|
| Python 3.13 | 27 ms / 40-45 ms | 105-109 ms / 115-160 ms |
| Python 3.9 | 24-26 ms / 26-30 ms | 106-108 ms / 169 ms |

Almost all of the difference is importing pydantic itself (89-169 ms in three
runs on the same machine); the classes' validators are not built at import
(`defer_build`).

The first construction of each class builds its validator (`defer_build`):
building the six shared test example specs the first time costs about
80-120 ms once per process; afterwards a round trip plus `validate()` of those
six specs takes about 6 ms instead of 3 ms.

## Writing a new model

1. Subclass `SpecModel` and decorate with `@model` (spec classes) or
   `@model(frozen=True, config=STRICT_CONFIG)` (descriptors checked on
   construction).
2. Spec classes: give every field a wire type: `Str("name")`,
   `OptStr("name")`, `Bool(...)`, `Int(...)`, `OptInt(...)`,
   `OptNumber(...)`, `JsonObject(...)`, `Nested(Cls)`, or
   `wire(parse_fn, PythonType)` with a `parse_fn(value)` that raises
   `SpecParseError` with paths relative to the field (use the `expect_*`
   helpers, `parse_object`/`parse_array` for containers and `fail()` for a
   specific code). The `what` argument is the name used in messages.
3. Set `__wire__ = WireShape("an axis", required=(...), drop=(...),
   order=(...))`: the message name, required keys, wire-only keys that are not
   `__init__` arguments, and the error order if it differs from declaration
   order. Set `__post_init_issue__ = (code, pointer)` if `__post_init__` can
   refuse a value.
4. Keep the Python constructor permissive: wrong types are `validate()`'s job.
   Put normalisation in `__post_init__`; raise `ValueError` there only for
   values that cannot be stored at all (NaN/inf, unparseable datetimes).
5. Write serialization explicitly in the module's `*_to_dict` and add the
   class to `json_schema()`; the wire-keys test fails until the schema and the
   class agree.
6. Annotations are evaluated at runtime by pydantic and must work on Python
   3.9: use `Optional[X]`, not `X | None` (ruff is configured with
   `keep-runtime-typing` for this), and make every name in an annotation
   importable from the module.
7. Add the new spec shape to an example so `tests/spec_hash_baseline.json`
   covers it (`python tools/spec_hashes.py --write`, on the commit *before*
   any change to existing classes).

## Verification

* `tools/spec_hashes.py` enumerates 35 specs (shared test examples, renderer
  test examples, the gallery: every mark type, every template example, the
  legacy bridge) and records `spec_hash`, the `indent=2` bytes, the parse
  round-trip hash, dataset `content_hash`es and `validate()` issues, plus the
  JSON schema, template catalogue, adapter capabilities and render-request
  dicts. The baseline was written from `main` before any change;
  `tests/test_spec_hash_baseline.py` compares on every run. The
  `vector_field_2d` template stamps numpy/sympy versions into provenance, so
  its exact entry is per environment and a "versions blanked" entry is
  compared everywhere.
* `tools/capture_spec_hashes.py` is a pytest plugin that hashes every spec the
  whole test suite serializes or validates (411 tests, 327 distinct specs);
  before/after runs of the full suite compare with zero differences.

## Gaps and follow-ups

* `validate()`'s type layer is still hand-written; it could run the same field
  annotations in a third, "check" mode and collect every error.
* `VisualizationArtifact.from_dict` and friends still read their (differently
  shaped) JSON by hand; construction now type-checks what they read.
* Views, themes and animation (unmerged branches) migrate when they land.
* `SpecParseError` still reports only the first problem; pydantic already
  collects all of them, so reporting every problem is a small change if the
  contract is widened.
