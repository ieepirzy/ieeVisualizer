# ruff: noqa: N999 - the package name predates this module
"""Typed, renderer-independent figure specification (FigureSpec).

Pure data model: importing this package never imports matplotlib or any other
renderer. See ``docs/SPEC.md`` for the reference and examples.
"""

from ._common import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    PatchError,
    SpecError,
    SpecParseError,
    SpecValidationError,
    ValidationIssue,
)
from .data import (
    DTYPES,
    Column,
    ColumnSchema,
    Dataset,
    DatasetRef,
    DataSnapshot,
    Provenance,
)
from .figure import (
    ANNOTATION_TYPES,
    LEGEND_POSITIONS,
    SCALES,
    Annotation,
    Axis,
    FigureSpec,
    Legend,
    ReferenceBand,
    ReferenceLine,
    TextAnnotation,
)
from .marks import (
    ALLOWED_CHANNELS,
    CHANNELS,
    COORDINATE_SYSTEMS,
    MARK_TYPES,
    REQUIRED_3D,
    REQUIRED_CHANNELS,
    ROLES,
    AreaMark,
    BarMark,
    Encoding,
    FunctionMark,
    HeatmapMark,
    HistogramMark,
    LineMark,
    Mark,
    QuiverMark,
    ScatterMark,
)
from .patch import apply_patch
from .schema import json_schema
from .serialize import from_dict, from_json, spec_hash, to_dict, to_json
from .validate import validate

__all__ = [
    "ALLOWED_CHANNELS",
    "ANNOTATION_TYPES",
    "CHANNELS",
    "COORDINATE_SYSTEMS",
    "DTYPES",
    "LEGEND_POSITIONS",
    "MARK_TYPES",
    "REQUIRED_3D",
    "REQUIRED_CHANNELS",
    "ROLES",
    "SCALES",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Annotation",
    "AreaMark",
    "Axis",
    "BarMark",
    "Column",
    "ColumnSchema",
    "DataSnapshot",
    "Dataset",
    "DatasetRef",
    "Encoding",
    "FigureSpec",
    "FunctionMark",
    "HeatmapMark",
    "HistogramMark",
    "Legend",
    "LineMark",
    "Mark",
    "PatchError",
    "Provenance",
    "QuiverMark",
    "ReferenceBand",
    "ReferenceLine",
    "ScatterMark",
    "SpecError",
    "SpecParseError",
    "SpecValidationError",
    "TextAnnotation",
    "ValidationIssue",
    "apply_patch",
    "from_dict",
    "from_json",
    "json_schema",
    "spec_hash",
    "to_dict",
    "to_json",
    "validate",
]
