"""ComponentTemplate: a versioned, validated recipe that turns data + parameters
into a :class:`~ieeVisualizer.spec.FigureSpec`.

A template declares, up front and in JSON-serializable form, what it needs:

* :class:`Parameter` - name, type, default, allowed values, description;
* :class:`ColumnRequirement` - which columns of the input data it reads, which
  parameter names each column, the dtypes it accepts and what it expects of
  the column's unit.

:meth:`ComponentTemplate.check` checks a build request against that
declaration without building anything and returns structured
:class:`~ieeVisualizer.spec.ValidationIssue` objects (the same class
``validate()`` returns). :meth:`ComponentTemplate.build` runs the same checks,
raises :class:`TemplateInputError` (a ``SpecError``, so ``.issues`` carries
them) if any is an error, builds the spec, stamps
``metadata["template"] = {"name", "version", "params", ...}`` on it and asserts
that ``validate()`` finds no errors before returning it.

Issue paths are JSON pointers into the *build request* in its payload form
``{"data": <snapshot>, "params": {...}}`` (the shape :meth:`example` returns):
``/params/<name>`` for a parameter, ``/data/columns/<column>`` for a column,
``/data`` for the dataset as a whole. Codes:

======================== ===========================================================
code                     meaning
======================== ===========================================================
unknown_parameter        a parameter the template does not define (with did-you-mean)
missing_parameter        a required parameter was not given
invalid_parameter        wrong type, or a value outside the allowed set/range
conflicting_parameters   two parameters that exclude each other were both given
missing_data             the template needs a dataset and none was given
invalid_data             the data cannot be read as a snapshot
unresolved_reference     a DatasetRef was given; templates need inline values
empty_dataset            the dataset has no rows
missing_column           a column named by a parameter is not in the data
wrong_dtype              a column's dtype is not one the template accepts
all_null_column          a column the template draws has no non-null value
null_in_key_column       a time/series/role column has nulls (rows cannot be placed)
invalid_column_values    a column holds values the template cannot interpret
mixed_units              columns drawn on one axis disagree on (or lack) a unit
unit_override_conflict   a unit override contradicts the column's declared unit
missing_unit             (warning) no unit is known for an axis
empty_series             (warning) a series has no values in one part of the figure
no_forecast_rows         (warning) a forecast split was requested but matched no rows
invalid_expression       an expression cannot be parsed safely
expression_too_large     an expression would need unbounded exact arithmetic
                         (e.g. a constant power with a huge exponent) or is too big
unknown_symbol           an expression uses a name that is neither a variable,
                         a parameter nor an allowed function
nonfinite_samples        (warning) an expression gave nan/inf at some grid points;
                         those samples are stored as null, never as a number
======================== ===========================================================

Warnings do not stop a build; they are recorded in
``metadata["template"]["warnings"]`` of the produced spec so the artifact
carries them.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Annotated, Any, Optional

from pydantic import AfterValidator

from ..spec import (
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    SpecError,
    SpecValidationError,
    ValidationIssue,
    validate,
)
from ..spec._common import STRICT_CONFIG, SpecModel, model, ptr, suggest
from ..spec.data import DTYPES, dataset_from_dict, dataset_to_dict

#: Parameter types and the JSON-Schema-ish type an agent should send.
PARAM_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "column": "string (column name)",
    "column_list": "array of strings (column names); a single string is accepted",
    "scalar": "number or ISO-8601 string",
    "range": "array [min, max] of two numbers, min < max",
    "grid": "integer n (n x n points) or array [nx, ny]",
    "number_map": "object {name: number}",
}

#: Unit expectations a :class:`ColumnRequirement` can declare.
UNIT_POLICIES = ("recommended", "not_applicable")


class TemplateInputError(SpecError):
    """A build request failed the template's input checks. ``.issues`` holds
    every error and warning found (warnings alone never raise this)."""


class TemplateRegistryError(SpecError):
    """A registry operation failed (unknown template/version, duplicate
    registration, malformed version)."""


def _one_of(choices: Any, what: str) -> Any:
    def check(v: Any) -> Any:
        if v not in choices:
            raise ValueError(f"unknown {what} {v!r}; use one of {', '.join(choices)}.")
        return v

    return AfterValidator(check)


def _dtypes(v: tuple[str, ...]) -> tuple[str, ...]:
    bad = [d for d in v if d not in DTYPES]
    if bad or not v:
        raise ValueError(f"dtypes must be a non-empty tuple of {', '.join(DTYPES)}; got {v!r}.")
    return v


#: Field types of the descriptors: a template that declares an unknown
#: parameter type, dtype or unit policy fails when its class is defined.
_ParamType = Annotated[str, _one_of(PARAM_TYPES, "parameter type")]
_UnitPolicy = Annotated[str, _one_of(UNIT_POLICIES, "unit policy")]
_Dtypes = Annotated[tuple[str, ...], AfterValidator(_dtypes)]


@model(frozen=True, config=STRICT_CONFIG)
class Parameter(SpecModel):
    """One template parameter.

    ``type`` is a key of :data:`PARAM_TYPES`. ``default`` is used when the
    caller omits the parameter (``None`` means "unset"); a parameter with
    ``required=True`` has no default. ``allowed`` restricts the value to a
    fixed set.
    """

    name: str
    type: _ParamType
    description: str
    default: Any = None
    required: bool = False
    allowed: Optional[tuple[Any, ...]] = None

    def __post_init__(self) -> None:
        if self.required and self.default is not None:
            raise ValueError(f"parameter {self.name!r} is required, so it cannot have a default.")

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "json_type": PARAM_TYPES[self.type],
            "required": self.required,
            "default": copy.deepcopy(self.default),
            "description": self.description,
        }
        if self.allowed is not None:
            out["allowed"] = list(self.allowed)
        return out


@model(frozen=True, config=STRICT_CONFIG)
class ColumnRequirement(SpecModel):
    """A column (or list of columns) the template reads from the input data.

    The column *name* is not fixed: ``parameter`` names the template parameter
    whose value is the column name (or list of names, for ``column_list``
    parameters). ``dtypes`` lists the accepted spec dtypes. ``unit``:
    ``"recommended"`` (a missing unit is reported as a warning unless the
    template has another source for it) or ``"not_applicable"``.
    """

    role: str
    parameter: str
    dtypes: _Dtypes
    description: str
    unit: _UnitPolicy = "recommended"
    drawn: bool = True  # all-null is an error for a column that is drawn

    def describe(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "parameter": self.parameter,
            "dtypes": list(self.dtypes),
            "unit": self.unit,
            "description": self.description,
        }


class Issues:
    """Collects issues during a check/build."""

    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []

    def error(self, path: str, code: str, message: str) -> None:
        self.items.append(ValidationIssue(path, code, message, "error"))

    def warn(self, path: str, code: str, message: str) -> None:
        self.items.append(ValidationIssue(path, code, message, "warning"))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.items if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.items if i.severity != "error"]


def param_path(name: str) -> str:
    return ptr("params", name)


def column_path(name: str) -> str:
    return ptr("data", "columns", name)


def _is_number(v: Any) -> bool:
    """A finite int or float (not a bool). NaN and infinities are refused
    here, in ``check()``, rather than later by the spec built from them."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an int beyond float64
        return False


def coerce_data(data: Any, issues: Issues) -> DataSnapshot | None:
    """Accept a :class:`DataSnapshot`, its serialized dict form
    (``{"type": "snapshot", "columns": {...}}``) or a plain column mapping
    ``{name: values}``. Returns a *copy*; the caller's object is never
    mutated."""
    if isinstance(data, DataSnapshot):
        try:
            return dataset_from_dict(dataset_to_dict(data), "/data")  # deep, normalised copy
        except SpecError as e:
            # A snapshot mutated after construction can hold values its own
            # constructor would refuse; report them instead of raising.
            for i in e.issues:
                issues.error(i.path, "invalid_data", i.message)
            return None
    if isinstance(data, DatasetRef):
        issues.error("/data", "unresolved_reference",
                     f"data is a reference ({data.kind} {data.id!r}); templates need inline "
                     "values. Resolve it to a DataSnapshot first.")
        return None
    if isinstance(data, Mapping):
        try:
            if data.get("type") == "snapshot" and isinstance(data.get("columns"), Mapping):
                return dataset_from_dict(dict(data), "/data")
            if data.get("type") == "ref":
                issues.error("/data", "unresolved_reference",
                             "data is a dataset reference; templates need inline values. "
                             "Resolve it to a snapshot first.")
                return None
            return DataSnapshot.from_columns(data)
        except SpecError as e:
            for i in e.issues:
                issues.error(i.path, "invalid_data", i.message)
            return None
        except (TypeError, ValueError) as e:
            issues.error("/data", "invalid_data",
                         f"data could not be read as columns: {e} Pass a DataSnapshot or "
                         "{column: [values, ...]}.")
            return None
    issues.error("/data", "invalid_data",
                 f"data must be a DataSnapshot, a snapshot dict or a {{column: values}} mapping, "
                 f"got {type(data).__name__}.")
    return None


class ComponentTemplate:
    """Base class for templates. Subclasses set the class attributes and
    implement :meth:`_check` (optional) and :meth:`_build`, plus
    :meth:`example`.

    ``data_required=False`` lets a template run without a dataset (e.g. a
    vector field from expressions); column requirements are then checked only
    when data is given.
    """

    name: str = ""
    version: str = ""
    title: str = ""
    description: str = ""
    parameters: tuple[Parameter, ...] = ()
    columns: tuple[ColumnRequirement, ...] = ()
    data_required: bool = True
    #: Free-text notes for the catalogue (output shape, honesty rules).
    notes: tuple[str, ...] = ()

    # ------------------------------------------------------------------ hooks
    def _check(self, data: DataSnapshot | None, params: dict[str, Any], issues: Issues) -> None:
        """Template-specific checks; add errors/warnings to ``issues``."""

    def _build(self, data: DataSnapshot | None, params: dict[str, Any],
               issues: Issues) -> FigureSpec:
        raise NotImplementedError

    def example(self) -> dict[str, Any]:
        """A ready build payload ``{"data": ..., "params": {...}}``."""
        raise NotImplementedError

    # ------------------------------------------------------------ public API
    def parameter(self, name: str) -> Parameter | None:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def check(self, data: Any = None, **params: Any) -> list[ValidationIssue]:
        """Check a build request without building. Never raises for bad
        input; returns every issue found (empty list = buildable)."""
        issues = Issues()
        self._run_checks(data, params, issues)
        return issues.items

    def build(self, data: Any = None, **params: Any) -> FigureSpec:
        """Build the FigureSpec. Raises :class:`TemplateInputError` if the
        request has errors, and ``SpecValidationError`` if the produced spec
        does not validate (a template bug, never silently returned)."""
        issues = Issues()
        snapshot, resolved = self._run_checks(data, params, issues)
        if issues.errors:
            raise TemplateInputError(issues.items)
        spec = self._build(snapshot, resolved, issues)
        if issues.errors:  # _build may still find data it cannot place
            raise TemplateInputError(issues.items)
        meta = dict(spec.metadata.get("template", {}))
        meta.update({"name": self.name, "version": self.version,
                     "params": copy.deepcopy(resolved)})
        if issues.warnings:
            meta["warnings"] = [i.to_dict() for i in issues.warnings]
        spec.metadata["template"] = meta
        errors = [i for i in validate(spec) if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
        return spec

    def build_payload(self, payload: Mapping[str, Any]) -> FigureSpec:
        """Build from ``{"data": ..., "params": {...}}`` (the :meth:`example`
        shape). Both keys are optional."""
        unknown = sorted(set(payload) - {"data", "params"})
        if unknown:
            raise TemplateInputError([ValidationIssue(
                ptr(unknown[0]), "invalid_data",
                f"a build payload has only 'data' and 'params'; got {unknown[0]!r}.")])
        return self.build(payload.get("data"), **dict(payload.get("params") or {}))

    def describe(self) -> dict[str, Any]:
        """JSON-serializable catalogue entry for agents."""
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "data": {
                "required": self.data_required,
                "accepts": ["DataSnapshot", "snapshot dict {type: snapshot, columns: {...}}",
                            "column mapping {name: [values]}"],
                "columns": [c.describe() for c in self.columns],
            },
            "parameters": [p.describe() for p in self.parameters],
            "payload_shape": {"data": "<snapshot or column mapping>", "params": "{name: value}"},
            "notes": list(self.notes),
            "example": self.example(),
        }

    # -------------------------------------------------------------- internals
    def _run_checks(self, data: Any, params: dict[str, Any],
                    issues: Issues) -> tuple[DataSnapshot | None, dict[str, Any]]:
        resolved = self._check_params(params, issues)
        snapshot = None
        if data is None:
            if self.data_required:
                issues.error("/data", "missing_data",
                             f"template {self.name!r} needs a dataset; pass data as a "
                             "DataSnapshot or {column: [values]}.")
        else:
            snapshot = coerce_data(data, issues)
            if snapshot is not None:
                self._check_columns(snapshot, resolved, issues)
        if not issues.errors:
            self._check(snapshot, resolved, issues)
        return snapshot, resolved

    def _check_params(self, params: dict[str, Any], issues: Issues) -> dict[str, Any]:
        known = {p.name for p in self.parameters}
        for name in sorted(set(params) - known):
            issues.error(param_path(name), "unknown_parameter",
                         f"template {self.name!r} has no parameter {name!r}."
                         f"{suggest(name, known)} Parameters: {', '.join(sorted(known))}.")
        resolved: dict[str, Any] = {}
        for p in self.parameters:
            if p.name in params and params[p.name] is not None:
                value = _coerce_param(p, params[p.name], issues)
            else:
                value = copy.deepcopy(p.default)
                if value is not None:  # normalise defaults the same way (e.g. grid 20 -> [20, 20])
                    value = _coerce_param(p, value, issues)
                if p.required and value is None:
                    issues.error(param_path(p.name), "missing_parameter",
                                 f"parameter {p.name!r} is required: {p.description}")
            resolved[p.name] = value
        return resolved

    def _check_columns(self, data: DataSnapshot, params: dict[str, Any], issues: Issues) -> None:
        if data.num_rows is None and data.columns:
            issues.error("/data", "invalid_data",
                         f"columns have different lengths {data.lengths()}; every column must "
                         "have one value per row (use null for missing values).")
            return
        if not data.columns or data.num_rows == 0:
            issues.error("/data", "empty_dataset",
                         "the dataset has no rows; there is nothing to draw. Supply data rather "
                         "than drawing an empty or zero series.")
            return
        for req in self.columns:
            value = params.get(req.parameter)
            if value is None:
                continue
            names = value if isinstance(value, list) else [value]
            for name in names:
                if not isinstance(name, str):
                    continue  # already reported as invalid_parameter
                col = data.columns.get(name)
                if col is None:
                    issues.error(
                        param_path(req.parameter), "missing_column",
                        f"{req.role} column {name!r} (parameter {req.parameter!r}) is not in "
                        f"the data.{suggest(name, data.columns)} Columns: "
                        f"{', '.join(sorted(data.columns))}.")
                    continue
                if col.dtype is not None and col.dtype not in req.dtypes:
                    issues.error(
                        column_path(name), "wrong_dtype",
                        f"{req.role} column {name!r} has dtype {col.dtype!r}; template "
                        f"{self.name!r} accepts {', '.join(req.dtypes)}. Convert the column "
                        "upstream or name a different column.")
                    continue
                if req.drawn and not col.non_null():
                    issues.error(
                        column_path(name), "all_null_column",
                        f"{req.role} column {name!r} has no non-null values, so nothing would "
                        "be drawn; supply data or leave the column out rather than plotting "
                        "it as empty or zero.")


def _coerce_param(p: Parameter, v: Any, issues: Issues) -> Any:
    """Type-check one parameter value; return the normalised value (or the
    raw value after recording an error)."""
    path = param_path(p.name)

    def bad(expected: str) -> Any:
        issues.error(path, "invalid_parameter",
                     f"parameter {p.name!r} must be {expected}, got {v!r}.")
        return v

    t = p.type
    if t in ("string", "column"):
        if not isinstance(v, str) or not v:
            return bad("a non-empty string")
    elif t == "column_list":
        if isinstance(v, str):
            v = [v]
        if (not isinstance(v, (list, tuple)) or not v
                or not all(isinstance(x, str) and x for x in v)):
            return bad("a non-empty list of column names")
        v = list(v)
        if len(set(v)) != len(v):
            return bad("a list of distinct column names")
    elif t == "integer":
        if not isinstance(v, int) or isinstance(v, bool):
            return bad("an integer")
    elif t == "number":
        if not _is_number(v):
            return bad("a finite number")
    elif t == "boolean":
        if not isinstance(v, bool):
            return bad("true or false")
    elif t == "scalar":
        if not (_is_number(v) or isinstance(v, str)):
            return bad("a finite number or an ISO-8601 string")
    elif t == "range":
        if (not isinstance(v, (list, tuple)) or len(v) != 2
                or not all(_is_number(x) for x in v) or not v[0] < v[1]):
            return bad("[min, max] with two finite numbers and min < max")
        v = [float(v[0]), float(v[1])]
    elif t == "grid":
        if isinstance(v, int) and not isinstance(v, bool):
            v = [v, v]
        if (not isinstance(v, (list, tuple)) or len(v) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
            return bad("an integer or [nx, ny]")
        v = list(v)
    elif t == "number_map":
        if not isinstance(v, Mapping) or not all(
                isinstance(k, str) and _is_number(x) for k, x in v.items()):
            return bad("an object mapping names to finite numbers")
        v = dict(v)
    else:  # pragma: no cover - template authoring error
        raise ValueError(f"unknown parameter type {t!r}")
    if p.allowed is not None and v not in p.allowed:
        issues.error(path, "invalid_parameter",
                     f"parameter {p.name!r} must be one of {list(p.allowed)}, got {v!r}."
                     f"{suggest(str(v), [str(a) for a in p.allowed])}")
    return v
