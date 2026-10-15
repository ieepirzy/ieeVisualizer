# Model classes (pydantic)

The spec classes (`FigureSpec`, marks, datasets, axes, legend, annotations,
provenance), `ValidationIssue`, the render contracts (`RenderRequest`,
`RenderResult`, `RenderWarning`, `Capabilities`), the artifact manifest
(`VisualizationArtifact`, `FileEntry`, `RenderVariant`, `DatasetRecord`) and the
template descriptors (`Parameter`, `ColumnRequirement`) are
[`pydantic.dataclasses`](https://docs.pydantic.dev/latest/concepts/dataclasses/)
(pydantic v2, 2.1.0 or newer). This note says why, what pydantic does and does
not do here, how parse errors become the library's structured issues, and how
to write a new model.

## Why

Before this change every spec class had two hand-maintained descriptions: the
dataclass fields and a procedural `*_from_dict` parser that re-listed every key,
its type and its error message (about 200 lines across `data.py`, `marks.py`
and `figure.py`). Now a field's annotation *is* its wire type:

```python
@model
class Axis(SpecModel):
    __wire__ = WireShape("an axis", order=("limits", "label", "unit", "scale"))

    label: OptStr("label") = None
    unit: OptStr("unit") = None
    scale: OptStr("scale") = None
    limits: wire(_parse_limits, "list[Scalar | None] | None") = None
```

`parse_model()` reads every class the same way from those annotations (key
check, field parsers in the old parser's order, pointer composition), and
pydantic builds the parsed object.

What did **not** change, and is pinned by tests:

* the JSON wire format, the canonical bytes of `to_json`, every `spec_hash` and
  `content_hash`, and the meaning of `schema_version` 1.0
  (`tests/spec_hash_baseline.json`, recorded from `main` before the migration;
  see [Verification](#verification));
* every public constructor: it *is* the stdlib dataclass `__init__` (see
  below), so it binds the same arguments, raises the same `TypeError`s, accepts
  and stores exactly the objects it is given (no checks, no conversion, no
  copies), raises a `__post_init__`'s own exception, and costs what it did.
  `inspect.signature` of each class and of its `__init__`, and the generated
  `__doc__` of classes without a docstring, are `main`'s
  (`tests/spec/main_signatures.json`); subclasses with their own `__init__`,
  positional `super().__init__(...)` and calling `__init__` again all behave
  as before. Spec classes are mutable; `ValidationIssue`, the render contracts
  and the template descriptors are frozen;
* the error contract: parse errors are `SpecParseError` (a `ValueError`) with
  RFC-6901 pointers and the same issue codes and messages, the same first
  problem, found at the same cost (parsing stops there); an exception the old
  parser let through unchanged (e.g. a `ValueError` from comparing a numpy
  array) still does; `validate()` never raises and returns the same codes and
  paths;
* `json_schema()` (still hand-written, see [JSON schema](#json-schema)).

Serialization (`to_dict`/`to_json`) stays explicit code, not pydantic's
serializer: the bytes are the contract (omitted `None`s, explicit defaults,
canonical floats and datetimes, re-normalised column values) and a generic
serializer would have to be configured into exactly that shape anyway.

What is visibly different: the classes' `__annotations__` and the
`dataclasses.fields(...)[i].type` strings are the wire declarations
(`'OptStr("label")'`) rather than the plain types (`'str | None'`), and the
classes have pydantic's attributes (`__pydantic_fields__`,
`__pydantic_validator__`, ...).

## Construct and parse

* **Construct** (`FigureSpec(...)`, `Column(...)`, `RenderRequest(...)` from
  Python). `pydantic.dataclasses.dataclass` lets the stdlib generate
  `__init__` and then replaces it with its own (`__init__(self, *args,
  **kwargs)` running its validator), both when decorating and again when it
  builds a deferred class. The `SpecModel` metaclass ignores exactly those
  two assignments (of `__init__` and of `__signature__`, when the value comes
  from pydantic), so the stdlib-generated `__init__` stays. pydantic's
  `__init__` would differ from the stdlib's in ways callers see: it counts an
  `init=False` field (a mark's or annotation's `type`) as a positional
  parameter, wraps exceptions, converts containers and costs 7-10 times as
  much. `model()` also restores what pydantic changes about the class: the
  `__init__` annotations are shown as they were written before the migration
  (each wire type carries that text) and a class without a docstring gets the
  stdlib's generated `__doc__`.

  So construction is as permissive as before: `DatasetRef(kind=5, id="x")`,
  `HistogramMark(bins=2.5)`, `ValidationIssue(1, "c", "m", "fatal")` and
  `Capabilities(..., max_points=numpy.int64(5))` construct, and `validate()`
  reports what a spec has wrong, with a pointer. The `__post_init__` hooks
  still normalise (column values, canonical floats, encoding shorthand, time
  coordinates) and refuse NaN/inf exactly as before.
* **Parse** (`from_dict`, `from_json`, `apply_patch`, template inputs):
  `parse_model()`, see below. It does not call `__init__`.

## Parsing

A spec class's field is `Annotated[Any, Wire(parse, text)]`: `parse(value)`
reads the JSON value (raising `SpecParseError` with a pointer *relative to the
field*), `text` is the Python type for signatures. pydantic sees `Any`, so it
neither checks nor converts nor copies a value (a 500,000-value column is not
walked again). The factories in `spec/_model.py`:

| factory | accepts | refuses |
|---|---|---|
| `Str(what)` / `OptStr(what)` | a string (or `null` for `OptStr`) | numbers, bools, `null` where not optional |
| `Bool(what)` | `true` / `false` | `1`, `"true"` |
| `Int(what)` / `OptInt(what)` | an integer; an integer-valued float (`200.0`) reads as `200` (JSON does not distinguish them) | `true`, `"5"`, `5.5`, NaN/inf, beyond float64 |
| `OptNumber(what)` | a finite number, stored as a canonical float (`-0.0` → `0.0`) | bools, strings, NaN/inf (`non_finite_number`), beyond float64 (`number_out_of_range`) |
| `JsonObject(what)` | a JSON object of JSON values, deep-copied | non-JSON values, NaN/inf anywhere inside |
| `Nested(cls)` | an object, parsed by `cls` | |
| `wire(parse, text)` | whatever `parse(value)` accepts | whatever it raises |

`parse_model(cls, data, name)`:

1. checks that `data` is an object, runs `WireShape.pre` (`schema_version`),
   and refuses unknown keys (with a did-you-mean) and missing required keys
   (`check_keys`);
2. runs the parser of each present field in `WireShape.order` (the order the
   pre-migration parser read them: e.g. a `FigureSpec` reads `id`, `title`,
   `caption`, `theme`, `coordinate_system`, `datasets`, `layers`, ...; an axis
   reads `limits` first), and stops at the first problem: the fields after it
   are not read, so `{"title": 5, "datasets": <a huge column>}` is refused
   without touching the column. A `SpecParseError` gets the field's pointer
   prepended (`at()`); container items are read in document order by
   `parse_object` / `parse_array`, which prepend the item's pointer, and a
   nested model by its own `parse_model`. So the issue for a bad column unit
   is `/datasets/d/columns/y/unit` however deep it sits. Any other exception
   from a parser propagates unchanged, as it did from the old parser;
3. hands the parsed values to pydantic (`build()`), which fills defaults and
   default factories, builds the object and runs `__post_init__`.

`SpecParseError` from `from_dict` carries one issue, as before. No error text
passes through pydantic: field parsers run before it, and a `__post_init__`
that raises during `build()` has its exception set aside and re-raised as the
original object (pydantic would render its text, which fails outright for a
key holding a lone surrogate such as `"\ud800"`). The one translation is the
old parser's: a class may declare `__post_init_issue__ = (code, pointer)`, and
a plain `ValueError` from its `__post_init__` becomes that issue (`Column`:
`invalid_values` at `/values`). If pydantic itself refused a value (a class
whose `WireShape` disagrees with its fields), the error still surfaces as a
`SpecParseError`, never as a raw `pydantic.ValidationError`.

`null` stays `null` (never `0` or `""`), and datetimes are canonicalised by the
same code as before (`Column.__post_init__`, `canonicalize_time_coordinates`).

The descriptor classes (`ValidationIssue`, render contracts, manifest, template
descriptors) have plain annotations (`Optional[str]`, `frozenset[str]`, ...)
and are only ever built by their constructors; the manifest's `from_dict`
methods read their JSON by hand, as before.

`SPEC_CONFIG` (`spec/_common.py`) is the configuration of every class:
`extra="forbid"` (unknown keys are refused by `check_keys` first, with the
library's message), `arbitrary_types_allowed`, `revalidate_instances="never"`
and `defer_build=True`, which builds a class's pydantic validator on first
parse and keeps `import ieeVisualizer.spec` from building ~20 schemas up front.

`tests/spec/test_spec_models.py` pins the mapping: one test per issue code
with its pointer, no coercion, parse order, constructor exceptions.
`tests/spec/test_spec_model_parity.py` pins the cases where an earlier version
of this change differed from `main` (subclass constructors, positional
`super().__init__`, stored objects, lone surrogates, early stop, signatures).

## JSON schema

`json_schema()` is still the hand-written draft-07 schema. pydantic's
generated schema (`TypeAdapter(FigureSpec).json_schema()`) has nothing to go
on: the spec fields are `Any` to pydantic, and the wire details (the `type`
discriminators of datasets, marks and annotations, encoding shorthand, enums,
the ISO-8601 pattern, `minItems`/`maxItems`) live in the parsers and the
hand-written schema. Instead of generating it,
`test_model_wire_keys_match_the_json_schema` checks that every model's wire
keys and required keys equal the schema's `properties` and `required`, so the
two cannot drift apart; the existing schema ↔ parser tests still pass.

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

Constructing from Python costs what it did (`LineMark("d", {"y": "c"})`
1.2 µs, `ValidationIssue(...)` 0.4 µs). The first parse of each class builds
its validator, once per process (about 20-30 ms for a typical spec); after
that `from_dict` of the first shared example takes about 225 µs instead of
205 µs, a 5,000-layer spec about 255 ms instead of 210 ms.

## Writing a new model

1. Subclass `SpecModel` and decorate with `@model` (or `@model(frozen=True)`).
2. Spec classes: give every field a wire type: `Str("name")`,
   `OptStr("name")`, `Bool(...)`, `Int(...)`, `OptInt(...)`,
   `OptNumber(...)`, `JsonObject(...)`, `Nested(Cls)`, or
   `wire(parse_fn, "python type")` with a `parse_fn(value)` that raises
   `SpecParseError` with paths relative to the field (use the `expect_*`
   helpers, `parse_object`/`parse_array` for containers and `fail()` for a
   specific code). The `what` argument is the name used in messages.
   Descriptor classes: plain annotations.
3. Set `__wire__ = WireShape("an axis", required=(...), drop=(...),
   order=(...))`: the message name, required keys, wire-only keys that are not
   `__init__` arguments, and the read order if it differs from declaration
   order. Set `__post_init_issue__ = (code, pointer)` if a `ValueError` from
   `__post_init__` should be reported as an issue while parsing.
4. Keep the Python constructor permissive: wrong types are `validate()`'s job.
   Put normalisation in `__post_init__`; raise `ValueError` there only for
   values that cannot be stored at all (NaN/inf, unparseable datetimes).
5. Write serialization explicitly in the module's `*_to_dict` and add the
   class to `json_schema()`; the wire-keys test fails until the schema and the
   class agree.
6. Annotations are evaluated at runtime by pydantic and must work on Python
   3.9: use `Optional[X]`, not `X | None` (ruff is configured with
   `keep-runtime-typing` for this; the `text` of a wire type is a string and
   may say `X | None`), and make every name in an annotation importable from
   the module.
7. Add the new spec shape to an example so `tests/spec_hash_baseline.json`
   covers it (`python tools/spec_hashes.py --write`, on the commit *before*
   any change to existing classes).

## Verification

* `tools/spec_hashes.py` enumerates 50 specs (shared test examples, renderer
  test examples, the gallery: every mark type, every template example, the
  themed examples, the legacy bridge and the network gallery) and records
  `spec_hash`, the `indent=2` bytes, the parse round-trip hash, dataset
  `content_hash`es and `validate()` issues, plus the JSON schema, template
  catalogue, adapter capabilities and render-request dicts. The baseline was
  written from `main`'s pre-migration code; `tests/test_spec_hash_baseline.py`
  compares on every run. Specs built with transcendental numpy functions are
  not bit-portable across CPUs (numpy's AVX-512 and AVX2 `exp` differ by 1 ulp
  on some inputs), so those are compared exactly only in a recorded
  environment and through a 12-significant-digit "portable" record elsewhere;
  every other spec is compared exactly everywhere (see the tool's docstring).
* `tests/spec/test_spec_constructors.py` constructs every model class
  positionally from its `dataclasses.fields` order and pins the bad-call
  `TypeError`s and the signature; `tests/spec/test_spec_model_parity.py`
  compares signatures and docs with `main`'s.
* Differential runs against `main` on Python 3.13 and 3.9: mutated specs
  through `from_dict`, `to_json`, `spec_hash` and `validate()` (50,256 cases,
  including numpy scalars, lone-surrogate keys, deep nesting), constructor
  calls with odd values for every field of every spec class (2,400) and
  for the descriptors (129 calls), dataclass protocols (pickle, `copy`,
  `replace`, `asdict`, equality, hashing, subclassing, exception chaining),
  threads. They agree with `main` except for the class `__annotations__` (see
  above) and one extra traceback frame (`model()`'s `__post_init__` guard)
  in an exception raised by a `__post_init__`.
* CI also runs the suite with the lowest supported pydantic (2.1.0) on Python
  3.9. pydantic 2.0 is not supported: its dataclass decorator makes the stdlib
  generate no `__init__`.
* `tools/capture_spec_hashes.py` is a pytest plugin that hashes every spec the
  whole test suite serializes or validates (565 tests, 429 distinct specs);
  before/after runs of the full suite compare with zero differences.

## Gaps and follow-ups

* `validate()`'s type layer is still hand-written; it could run the same field
  parsers in a "check" mode that collects every error.
* `VisualizationArtifact.from_dict` and friends still read their (differently
  shaped) JSON by hand.
* The `network` package (and the `themes` value classes) keep plain
  dataclasses; they migrate in a follow-up. The theme fields `main` added to
  `RenderResult` and the manifest are declared on the pydantic models.
* `SpecParseError` still reports only the first problem (and parsing stops
  there, as before); reporting every problem would mean reading every field,
  so it is a contract and cost change, not a pydantic switch.
