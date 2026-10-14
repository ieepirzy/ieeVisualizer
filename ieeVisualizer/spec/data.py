"""Datasets a FigureSpec can bind to: inline snapshots and external references.

* :class:`DataSnapshot` holds small inline columnar data. Every column carries
  its own ``dtype`` and ``unit`` (plus optional description, denominator and
  aggregation window, the provenance facts reporting needs per metric), and
  the snapshot carries a :class:`Provenance` block (source, query, retrieval
  time, source version, timezone, run id).
* :class:`DatasetRef` points at data held elsewhere (a query, a stored
  artifact, a file) without copying it. It may declare the column schema so
  the validator can still check encodings and units.

Missing values are ``None`` / JSON ``null`` and are never coerced to ``0``.
Float ``NaN`` and numpy ``NaT`` are normalised to ``None`` because JSON has no
NaN; ``+/-inf`` is rejected (it is a value, not a missing one, and JSON cannot
hold it).

``DataSnapshot.content_hash()`` is a SHA-256 over the canonical JSON of the
columns (values, dtype, unit, description, denominator, aggregation). The
provenance block is deliberately *excluded*: re-fetching byte-identical data
at a later time yields the same hash, while any change in a value or in a
column's meaning (unit, dtype) changes it.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Union

from ._common import (
    check_keys,
    expect_dict,
    expect_list,
    expect_str,
    fail,
    ptr_join,
    sha256_of,
)

DTYPES = ("float", "int", "bool", "str", "datetime")


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        # NaN and NaT (numpy/pandas) are the values not equal to themselves.
        return bool(v != v)  # noqa: PLR0124
    except Exception:  # noqa: BLE001 - objects whose != raises are treated as present
        return False


def _from_numpy(values: Any) -> tuple[list[Any], str | None]:
    """Convert a numpy array; numpy is imported lazily, only for datetime64."""
    if values.ndim != 1:
        raise ValueError(f"column values must be 1-dimensional, got shape {values.shape}.")
    kind = values.dtype.kind
    if kind == "M":  # datetime64: tolist() would give ints for ns precision
        import numpy as np

        strings = np.datetime_as_string(values).tolist()  # keeps dtype precision
        return [None if s == "NaT" else s for s in strings], "datetime"
    if kind == "m":
        raise ValueError(
            "timedelta64 columns are not supported; convert to a float column with an "
            "explicit unit (e.g. seconds) instead."
        )
    return values.tolist(), None


def normalize_values(values: Any, dtype: str | None) -> tuple[list[Any], str | None]:
    """Return ``(json_safe_list, dtype)``; infer ``dtype`` when not given.

    Raises ``ValueError`` with a fix-oriented message on mixed or
    unrepresentable values.
    """
    if dtype is not None and dtype not in DTYPES:
        raise ValueError(f"unknown dtype {dtype!r}; use one of {', '.join(DTYPES)}.")
    inferred: str | None = None
    if hasattr(values, "dtype") and hasattr(values, "tolist") and hasattr(values, "ndim"):
        raw, inferred = _from_numpy(values)
    elif isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise TypeError("column values must be a list/array, not a scalar or string.")
    else:
        raw = list(values.tolist() if hasattr(values, "tolist") else values)

    out: list[Any] = []
    kinds = set()
    for i, v in enumerate(raw):
        if hasattr(v, "item") and not isinstance(v, (str, bytes)):
            v = v.item()  # numpy scalar -> python scalar
        if _is_missing(v):
            out.append(None)
            continue
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            if math.isinf(v):
                raise ValueError(
                    f"value at index {i} is {v}; infinities cannot be stored. Use null for a "
                    "missing value or fix the computation upstream."
                )
            kinds.add("float")
        elif isinstance(v, str):
            kinds.add("str")
        elif isinstance(v, (_dt.datetime, _dt.date)):
            v = v.isoformat()
            kinds.add("datetime")
        else:
            # ValueError (not TypeError) so from_dict reports it as invalid_values.
            raise ValueError(  # noqa: TRY004
                f"value at index {i} has unsupported type {type(v).__name__}."
            )
        out.append(v)

    dtype = dtype or inferred or _infer(kinds)
    _check_compatible(kinds, dtype)
    if dtype == "float":
        out = [None if v is None else float(v) for v in out]
    return out, dtype


def _infer(kinds: set) -> str | None:
    if not kinds:
        return None  # all-null column: dtype unknown, never guessed
    if kinds <= {"int", "float"}:
        return "float" if "float" in kinds else "int"
    if len(kinds) == 1:
        return next(iter(kinds))
    raise ValueError(
        f"column mixes value types {sorted(kinds)}; give every value the same type "
        "(use null for missing values)."
    )


_ALLOWED_KINDS = {
    "float": {"int", "float"},
    "int": {"int"},
    "bool": {"bool"},
    "str": {"str"},
    "datetime": {"str", "datetime"},
}


def _check_compatible(kinds: set, dtype: str | None) -> None:
    if dtype is None:
        return
    bad = kinds - _ALLOWED_KINDS[dtype]
    if bad:
        raise ValueError(
            f"dtype {dtype!r} cannot hold values of type {sorted(bad)}; convert the values or "
            "change the dtype."
        )


@dataclass
class ColumnSchema:
    """Column description without values (used by :class:`DatasetRef`)."""

    dtype: str | None = None
    unit: str | None = None
    description: str | None = None
    denominator: str | None = None
    aggregation: str | None = None


@dataclass
class Column:
    """One inline column. ``values`` is normalised on construction."""

    values: list[Any]
    dtype: str | None = None
    unit: str | None = None
    description: str | None = None
    denominator: str | None = None
    aggregation: str | None = None

    def __post_init__(self) -> None:
        self.values, self.dtype = normalize_values(self.values, self.dtype)

    def non_null(self) -> list[Any]:
        return [v for v in self.values if v is not None]


@dataclass
class Provenance:
    """Where a snapshot came from. All fields optional; ``extra`` is free-form
    JSON for host-specific facts (e.g. permission scope applied upstream)."""

    source: str | None = None
    query: str | None = None
    retrieved_at: str | None = None
    source_version: str | None = None
    timezone: str | None = None
    run_id: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSnapshot:
    """Inline columnar data with per-column units and dataset provenance."""

    columns: dict[str, Column] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)

    @classmethod
    def from_columns(
        cls,
        columns: Mapping[str, Any],
        units: Mapping[str, str | None] | None = None,
        dtypes: Mapping[str, str | None] | None = None,
        provenance: Provenance | None = None,
    ) -> DataSnapshot:
        """Build from ``{name: values}`` (lists or numpy arrays)."""
        units = units or {}
        dtypes = dtypes or {}
        for name in list(units) + list(dtypes):
            if name not in columns:
                raise ValueError(f"unit/dtype given for unknown column {name!r}.")
        cols = {
            name: Column(values=vals, dtype=dtypes.get(name), unit=units.get(name))
            for name, vals in columns.items()
        }
        return cls(columns=cols, provenance=provenance or Provenance())

    def lengths(self) -> dict[str, int]:
        return {name: len(c.values) for name, c in self.columns.items()}

    @property
    def num_rows(self) -> int | None:
        """Row count, or ``None`` when columns disagree or there are none."""
        lens = set(self.lengths().values())
        return lens.pop() if len(lens) == 1 else None

    def content_hash(self) -> str:
        """``sha256:<hex>`` over the canonical JSON of the columns only."""
        return sha256_of({"columns": {n: _column_to_dict(c) for n, c in self.columns.items()}})


@dataclass
class DatasetRef:
    """Reference to data held elsewhere. ``kind`` is free text; recommended
    values are ``query``, ``artifact``, ``file``, ``table``, ``run``."""

    kind: str
    id: str
    query: str | None = None
    version: str | None = None
    retrieved_at: str | None = None
    columns: dict[str, ColumnSchema] | None = None


Dataset = Union[DataSnapshot, DatasetRef]

# --------------------------------------------------------------------------
# (De)serialization. Optional fields that are None are omitted.
# --------------------------------------------------------------------------

_SCHEMA_FIELDS = ("dtype", "unit", "description", "denominator", "aggregation")
_PROV_FIELDS = ("source", "query", "retrieved_at", "source_version", "timezone", "run_id", "notes")


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _column_to_dict(c: Column) -> dict[str, Any]:
    d = {"values": list(c.values)}
    d.update(_drop_none({k: getattr(c, k) for k in _SCHEMA_FIELDS}))
    return d


def dataset_to_dict(ds: Dataset) -> dict[str, Any]:
    if isinstance(ds, DataSnapshot):
        prov = _drop_none({k: getattr(ds.provenance, k) for k in _PROV_FIELDS})
        if ds.provenance.extra:
            prov["extra"] = dict(ds.provenance.extra)
        out: dict[str, Any] = {
            "type": "snapshot",
            "columns": {n: _column_to_dict(c) for n, c in ds.columns.items()},
        }
        if prov:
            out["provenance"] = prov
        return out
    if isinstance(ds, DatasetRef):
        out = {"type": "ref", "kind": ds.kind, "id": ds.id}
        out.update(_drop_none({k: getattr(ds, k) for k in ("query", "version", "retrieved_at")}))
        if ds.columns is not None:
            out["columns"] = {
                n: _drop_none({k: getattr(s, k) for k in _SCHEMA_FIELDS})
                for n, s in ds.columns.items()
            }
        return out
    raise TypeError(f"not a dataset: {type(ds).__name__}")


def _parse_schema_fields(d: dict[str, Any], path: str) -> dict[str, str | None]:
    return {k: expect_str(d.get(k), ptr_join(path, k), k, optional=True) for k in _SCHEMA_FIELDS}


def dataset_from_dict(d: Any, path: str) -> Dataset:
    d = expect_dict(d, path, "a dataset")
    kind = d.get("type")
    if kind == "snapshot":
        check_keys(d, path, "a snapshot dataset", ("type", "columns", "provenance"), ("columns",))
        cols_in = expect_dict(d["columns"], ptr_join(path, "columns"), "columns")
        cols: dict[str, Column] = {}
        for name, cd in cols_in.items():
            cp = ptr_join(path, "columns", name)
            cd = expect_dict(cd, cp, f"column {name!r}")
            check_keys(cd, cp, f"column {name!r}", ("values",) + _SCHEMA_FIELDS, ("values",))
            values = expect_list(cd["values"], ptr_join(cp, "values"), "values")
            fields = _parse_schema_fields(cd, cp)
            try:
                cols[name] = Column(values=values, **fields)
            except ValueError as e:
                raise fail(ptr_join(cp, "values"), "invalid_values", str(e)) from None
        prov = Provenance()
        if "provenance" in d:
            pp = ptr_join(path, "provenance")
            pd = expect_dict(d["provenance"], pp, "provenance")
            check_keys(pd, pp, "provenance", _PROV_FIELDS + ("extra",))
            kw = {k: expect_str(pd.get(k), ptr_join(pp, k), k, optional=True) for k in _PROV_FIELDS}
            extra = expect_dict(pd.get("extra", {}), ptr_join(pp, "extra"), "provenance.extra")
            prov = Provenance(extra=dict(extra), **kw)
        return DataSnapshot(columns=cols, provenance=prov)
    if kind == "ref":
        check_keys(
            d, path, "a ref dataset",
            ("type", "kind", "id", "query", "version", "retrieved_at", "columns"),
            ("kind", "id"),
        )
        columns = None
        if "columns" in d:
            columns = {}
            cin = expect_dict(d["columns"], ptr_join(path, "columns"), "columns")
            for name, cd in cin.items():
                cp = ptr_join(path, "columns", name)
                cd = expect_dict(cd, cp, f"column {name!r}")
                check_keys(cd, cp, f"column schema {name!r}", _SCHEMA_FIELDS)
                columns[name] = ColumnSchema(**_parse_schema_fields(cd, cp))
        return DatasetRef(
            kind=expect_str(d["kind"], ptr_join(path, "kind"), "kind"),
            id=expect_str(d["id"], ptr_join(path, "id"), "id"),
            query=expect_str(d.get("query"), ptr_join(path, "query"), "query", optional=True),
            version=expect_str(d.get("version"), ptr_join(path, "version"), "version", True),
            retrieved_at=expect_str(
                d.get("retrieved_at"), ptr_join(path, "retrieved_at"), "retrieved_at", True
            ),
            columns=columns,
        )
    raise fail(
        ptr_join(path, "type"),
        "invalid_dataset_type",
        f"dataset type must be 'snapshot' (inline columns) or 'ref' (external reference), "
        f"got {kind!r}.",
    )
