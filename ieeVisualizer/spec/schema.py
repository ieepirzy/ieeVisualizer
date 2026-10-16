"""JSON Schema (draft-07) for the serialized FigureSpec.

The schema describes the *structural* contract that :func:`from_dict`
enforces (types, allowed keys, enum values, mark/annotation shapes). Semantic
rules that need cross-references (an encoding naming an existing column, unit
agreement across layers, log scales vs data) are only checked by
:func:`validate`; a document can satisfy this schema and still have
validation errors.

Two parser rules have no JSON-Schema equivalent and are enforced only by
:func:`from_dict`/:func:`from_json`: NaN/Infinity are refused (they are not
JSON numbers, but Python's ``json`` module reads them), and datetime strings
must name a real date/time (the schema checks their ISO-8601 *form* only, so
``2026-02-30`` passes the schema and fails the parser).
"""

from __future__ import annotations

import copy
from typing import Any

from ._common import SUPPORTED_SCHEMA_VERSIONS
from .data import DTYPES, ISO_DATETIME_PATTERN
from .figure import LEGEND_POSITIONS, SCALES
from .marks import (
    CHANNELS,
    COORDINATE_SYSTEMS,
    INTERVAL_KINDS,
    INTERVAL_MARKS,
    LEVEL_KINDS,
    MARK_TYPES,
    MULTIPLIER_KINDS,
    ORIENTATIONS,
    ROLES,
)

_STR = {"type": "string"}
_OPT_STR = {"type": ["string", "null"]}
_NUM = {"type": "number"}
_SCALAR = {"type": ["number", "string", "null"]}


def _obj(props: dict[str, Any], required=(), **extra: Any) -> dict[str, Any]:
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = list(required)
    out.update(extra)
    return out


_COLUMN_META = {
    "dtype": {"enum": list(DTYPES) + [None]},
    "unit": _OPT_STR,
    "description": _OPT_STR,
    "denominator": _OPT_STR,
    "aggregation": _OPT_STR,
}

_ENCODING = {
    "oneOf": [
        _STR,
        _obj({"field": _STR, "title": _OPT_STR}, ["field"]),
    ]
}

_MARK_BASE = {
    "data": _OPT_STR,
    "encoding": {
        "type": "object",
        "propertyNames": {"enum": list(CHANNELS)},
        "additionalProperties": {"$ref": "#/definitions/encoding"},
    },
    "role": {"enum": list(ROLES)},
    "name": _OPT_STR,
    "style": {"type": "object"},
}

_MARK_EXTRA: dict[str, dict[str, Any]] = {
    "bar": {"orientation": {"enum": list(ORIENTATIONS)}},
    "histogram": {
        "bins": {"type": ["integer", "null"], "minimum": 1},
        "bin_edges": {"type": ["array", "null"], "items": _NUM, "minItems": 2},
        "density": {"type": "boolean"},
    },
    "quiver": {"scale": {"type": ["number", "null"]}, "normalize": {"type": "boolean"}},
    "function": {
        "expression": _OPT_STR,
        "variables": {"type": "array", "items": _STR, "minItems": 1},
        "domain": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": _NUM, "minItems": 2, "maxItems": 2},
        },
        "samples": {"type": "integer", "minimum": 2},
        "parameters": {"type": "object", "additionalProperties": _NUM},
    },
}


_INTERVAL_PROPS = {
    "interval_kind": {"enum": list(INTERVAL_KINDS) + [None]},
    "interval_level": {"type": ["number", "null"], "exclusiveMinimum": 0, "exclusiveMaximum": 1},
    "interval_multiplier": {"type": ["number", "null"], "exclusiveMinimum": 0},
}


def _required_number(key: str) -> dict[str, Any]:
    return {"required": [key], "properties": {key: {"type": "number"}}}


def _absent_or_null(key: str) -> dict[str, Any]:
    return {"properties": {key: {"type": "null"}}}


#: An interval states its kind, and its level or SE/SD multiplier where the
#: kind has one (``validate`` reports the same with JSON pointers).
_INTERVAL_RULES = [
    {"if": {"required": ["interval_kind"],
            "properties": {"interval_kind": {"enum": list(LEVEL_KINDS)}}},
     "then": dict(_required_number("interval_level"),
                  **{"not": {"required": ["interval_multiplier"],
                             "properties": {"interval_multiplier": {"type": "number"}}}})},
    {"if": {"required": ["interval_kind"],
            "properties": {"interval_kind": {"enum": list(MULTIPLIER_KINDS)}}},
     "then": {"allOf": [_required_number("interval_multiplier"),
                        _absent_or_null("interval_level")]}},
    {"if": {"required": ["interval_kind"], "properties": {"interval_kind": {"const": "min-max"}}},
     "then": {"allOf": [_absent_or_null("interval_level"),
                        _absent_or_null("interval_multiplier")]}},
]


def _mark_schema(mtype: str) -> dict[str, Any]:
    props = {"type": {"const": mtype}}
    props.update(_MARK_BASE)
    props.update(_MARK_EXTRA.get(mtype, {}))
    if mtype not in INTERVAL_MARKS:
        return _obj(props, ["type"])
    props.update(_INTERVAL_PROPS)
    kind = {"required": ["interval_kind"],
            "properties": {"interval_kind": {"enum": list(INTERVAL_KINDS)}}}
    if mtype in ("band", "errorbar"):
        rules = [kind]
    else:  # a line/scatter/bar draws an interval only when its bounds are bound
        bounds = {"required": ["encoding"], "properties": {"encoding": {
            "anyOf": [{"required": ["y_lower"]}, {"required": ["y_upper"]}]}}}
        rules = [{"if": bounds, "then": kind}]
    return _obj(props, ["type"], allOf=rules + _INTERVAL_RULES)


_ANNOTATIONS = {
    "text": {"text": _STR, "x": _SCALAR, "y": _SCALAR, "z": _SCALAR},
    "reference_line": {"axis": {"enum": ["x", "y", "z"]}, "value": _SCALAR},
    "reference_band": {"axis": {"enum": ["x", "y", "z"]}, "start": _SCALAR, "end": _SCALAR},
}


def _annotation_schema(atype: str) -> dict[str, Any]:
    props = {"type": {"const": atype}, "label": _OPT_STR, "style": {"type": "object"}}
    props.update(_ANNOTATIONS[atype])
    return _obj(props, ["type"])


_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://github.com/ieepirzy/ieeVisualizer/schema/figurespec-1.0.json",
    "title": "ieeVisualizer FigureSpec",
    "definitions": {
        "encoding": _ENCODING,
        "column": _obj(
            dict({"values": {"type": "array"}}, **_COLUMN_META), ["values"],
            **{
                "if": {"properties": {"dtype": {"const": "datetime"}}, "required": ["dtype"]},
                "then": {"properties": {"values": {"items": {
                    "type": ["string", "null"], "pattern": ISO_DATETIME_PATTERN}}}},
            },
        ),
        "snapshot": _obj(
            {
                "type": {"const": "snapshot"},
                "columns": {"type": "object",
                            "additionalProperties": {"$ref": "#/definitions/column"}},
                "provenance": _obj({
                    "source": _OPT_STR, "query": _OPT_STR, "retrieved_at": _OPT_STR,
                    "source_version": _OPT_STR, "timezone": _OPT_STR, "run_id": _OPT_STR,
                    "notes": _OPT_STR, "extra": {"type": "object"},
                }),
            },
            ["type", "columns"],
        ),
        "ref": _obj(
            {
                "type": {"const": "ref"}, "kind": _STR, "id": _STR, "query": _OPT_STR,
                "version": _OPT_STR, "retrieved_at": _OPT_STR,
                "columns": {"type": "object", "additionalProperties": _obj(_COLUMN_META)},
            },
            ["type", "kind", "id"],
        ),
        "axis": _obj({
            "label": _OPT_STR, "unit": _OPT_STR, "scale": {"enum": list(SCALES) + [None]},
            "limits": {"type": ["array", "null"], "items": _SCALAR, "minItems": 2, "maxItems": 2},
        }),
        "mark": {"oneOf": [_mark_schema(t) for t in MARK_TYPES]},
        "annotation": {"oneOf": [_annotation_schema(t) for t in _ANNOTATIONS]},
    },
    **_obj(
        {
            "schema_version": {"enum": list(SUPPORTED_SCHEMA_VERSIONS)},
            "id": _OPT_STR,
            "title": _OPT_STR,
            "caption": _OPT_STR,
            "theme": _OPT_STR,
            "coordinate_system": {"enum": list(COORDINATE_SYSTEMS)},
            "axes": {"type": "object", "propertyNames": {"enum": ["x", "y", "z"]},
                     "additionalProperties": {"$ref": "#/definitions/axis"}},
            "datasets": {"type": "object", "additionalProperties": {"oneOf": [
                {"$ref": "#/definitions/snapshot"}, {"$ref": "#/definitions/ref"}]}},
            "layers": {"type": "array", "items": {"$ref": "#/definitions/mark"}},
            "legend": _obj({"show": {"type": "boolean"},
                            "position": {"enum": list(LEGEND_POSITIONS)}, "title": _OPT_STR}),
            "annotations": {"type": "array", "items": {"$ref": "#/definitions/annotation"}},
            "metadata": {"type": "object"},
        },
        ["schema_version"],
    ),
}


def json_schema() -> dict[str, Any]:
    """Return the FigureSpec JSON Schema (a fresh deep copy)."""
    return copy.deepcopy(_SCHEMA)
