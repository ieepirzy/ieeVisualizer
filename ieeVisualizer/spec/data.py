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
hold it). ``-0.0`` is stored as ``0.0`` (they compare equal, so they must hash
equally).

Datetime values have exactly one stored form, whatever they were built from
(``datetime``/``date`` objects, numpy ``datetime64`` of any unit, or ISO-8601
strings)::

    YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM]

Seconds are always written; the fraction is written, as exactly six digits,
only when it is non-zero; the UTC offset is written only for timezone-aware
values and is kept as given (``Z`` becomes ``+00:00``; instants are not
converted to UTC). So ``datetime(2026, 1, 1)``, ``date(2026, 1, 1)``,
``"2026-01-01"`` and ``datetime64("2026-01-01", "ns")`` all store
``"2026-01-01T00:00:00"``. Strings are parsed strictly: a date
``YYYY-MM-DD``, optionally followed by ``T`` (or a space) and ``HH:MM``,
``:SS``, ``.fraction`` and an offset ``Z``/``+HH:MM``. Anything else, an
impossible date, or a non-zero sub-microsecond part (which would have to be
truncated) is rejected. A column may not mix timezone-aware and naive values;
that is rejected on construction (a parse error from :func:`from_dict`),
because the two cannot be ordered on one axis.

``DataSnapshot.content_hash()`` is a SHA-256 over the canonical JSON of the
columns (values, dtype, unit, description, denominator, aggregation). The
provenance block is deliberately *excluded*: re-fetching byte-identical data
at a later time yields the same hash, while any change in a value or in a
column's meaning (unit, dtype) changes it.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Union

from ._common import (
    SpecParseError,
    beyond_float64,
    check_keys,
    copy_json,
    expect_dict,
    expect_list,
    expect_str,
    fail,
    out_of_range_error,
    ptr_join,
    sha256_of,
)

DTYPES = ("float", "int", "bool", "str", "datetime")

#: Accepted ISO-8601 input for datetime columns (also used by the JSON schema;
#: written so it means the same in Python ``re`` and ECMA-262).
ISO_DATETIME_PATTERN = (
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:[.]([0-9]+))?)?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})?)?$"
)
_ISO_RE = re.compile(ISO_DATETIME_PATTERN)


def parse_iso_datetime(s: str) -> _dt.datetime:
    """Parse one datetime string in the accepted form (see the module docstring).

    Works the same on every supported Python (3.9's ``fromisoformat`` lacks
    ``Z`` and most fraction lengths, so it is not used directly).
    """
    m = _ISO_RE.fullmatch(s)
    if m is None and re.match(r"[+-]?[0-9]{5,}-|-[0-9]", s):
        raise ValueError(
            f"{s!r} has a year outside 0001-9999, which cannot be stored; only four-digit "
            "years are supported."
        )
    if m is None:
        raise ValueError(
            f"{s!r} is not an ISO-8601 date/time; use YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS[.ffffff]] "
            "with an optional Z or +HH:MM offset."
        )
    y, mo, d, hh, mi, ss, frac, off = m.groups()
    frac = frac or ""
    if frac[6:].strip("0"):
        raise ValueError(
            f"{s!r} has sub-microsecond precision, which cannot be stored without truncating "
            "it; round to whole microseconds first."
        )
    tz = None
    if off == "Z":
        tz = _dt.timezone.utc
    elif off:
        sign = -1 if off[0] == "-" else 1
        tz = _dt.timezone(sign * _dt.timedelta(hours=int(off[1:3]), minutes=int(off[4:6])))
    return _dt.datetime(
        int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0),
        int(frac[:6].ljust(6, "0")), tzinfo=tz,
    )


def format_iso_datetime(d: _dt.datetime) -> str:
    """The canonical stored form of ``d`` (see the module docstring)."""
    if getattr(d, "nanosecond", 0):  # pandas.Timestamp
        raise ValueError(
            f"{d!r} has sub-microsecond precision, which cannot be stored without truncating "
            "it; round to whole microseconds first."
        )
    off = d.utcoffset()
    if off is not None and off % _dt.timedelta(minutes=1):
        raise ValueError(
            f"{d!r} has a UTC offset of {off}, which is not a whole number of minutes and "
            "cannot be written as +HH:MM; convert it to another timezone first."
        )
    return d.isoformat(timespec="microseconds" if d.microsecond else "seconds")


def _canon_datetimes(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    aware: set[bool] = set()
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        try:
            if isinstance(v, str):
                v = parse_iso_datetime(v)
            elif not isinstance(v, _dt.datetime):  # a date: midnight
                v = _dt.datetime.combine(v, _dt.time())
            text = format_iso_datetime(v)
        except ValueError as e:
            raise ValueError(f"value at index {i}: {e}") from None
        aware.add(v.utcoffset() is not None)
        if len(aware) > 1:
            raise ValueError(
                f"value at index {i}: the column mixes timezone-aware and naive datetimes; "
                "give every value an offset (e.g. +00:00) or none, so they can be ordered."
            )
        out.append(text)
    return out


def _np_datetimes_as_strings(values: Any) -> list[Any]:
    """ISO strings (``None`` for NaT) for a datetime64 array or scalar, at the
    value's own precision; month/year/week units are taken at their first day.

    numpy prints some units in forms that are not ISO-8601 (``Y`` as ``2026``,
    ``M`` as ``2026-01``, ``h`` as ``2026-01-01T05``), so those are first cast
    to a finer unit that prints in full (``Y``/``M``/``W`` to days, ``h`` to
    minutes); the cast is exact. Every other unit (``D``, ``m``, ``s`` and the
    sub-second units, with any multiplier) already prints as ISO-8601."""
    import numpy as np

    unit = np.datetime_data(values.dtype)[0]
    if unit in ("Y", "M", "W"):
        values = values.astype("datetime64[D]")
    elif unit == "h":
        values = values.astype("datetime64[m]")
    strings = np.atleast_1d(np.datetime_as_string(values)).tolist()
    return [None if s == "NaT" else s for s in strings]


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
        return _np_datetimes_as_strings(values), "datetime"
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
        if getattr(getattr(v, "dtype", None), "kind", None) == "M":
            # numpy datetime64 scalar: .item() would give an int at ns precision
            (v,) = _np_datetimes_as_strings(v)
            if v is not None:
                kinds.add("datetime")
                out.append(v)
                continue
        elif hasattr(v, "item") and not isinstance(v, (str, bytes)):
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
        for i, v in enumerate(out):
            if beyond_float64(v):  # e.g. 10**400 among floats: no float64 holds it
                raise out_of_range_error(f"/values/{i}", f"value at index {i}", v)
        out = [None if v is None else float(v) + 0.0 for v in out]  # -0.0 -> 0.0
    elif dtype == "datetime":
        out = _canon_datetimes(out)
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


def _canonical_values(c: Column) -> list[Any]:
    """``c.values`` in canonical form. Values are normalised on construction,
    but a list mutated afterwards may not be (an int appended to a float
    column, a date-only string, a NaN): re-normalising here keeps equal data
    hashing equally. Values that cannot be normalised are written unchanged,
    so an invalid spec can still be serialized (and patched); :func:`validate`
    reports them."""
    try:
        return normalize_values(c.values, c.dtype)[0]
    except (ValueError, TypeError):
        try:
            return list(c.values)
        except TypeError:
            return c.values


def _column_to_dict(c: Column) -> dict[str, Any]:
    d = {"values": _canonical_values(c)}
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
            except SpecParseError as e:  # number_out_of_range; path relative to the column
                i = e.issues[0]
                raise fail(cp + i.path, i.code, i.message) from None
            except ValueError as e:
                raise fail(ptr_join(cp, "values"), "invalid_values", str(e)) from None
        prov = Provenance()
        if "provenance" in d:
            pp = ptr_join(path, "provenance")
            pd = expect_dict(d["provenance"], pp, "provenance")
            check_keys(pd, pp, "provenance", _PROV_FIELDS + ("extra",))
            kw = {k: expect_str(pd.get(k), ptr_join(pp, k), k, optional=True) for k in _PROV_FIELDS}
            ep = ptr_join(pp, "extra")
            extra = copy_json(expect_dict(pd.get("extra", {}), ep, "provenance.extra"), ep,
                              "provenance.extra")
            prov = Provenance(extra=extra, **kw)
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
