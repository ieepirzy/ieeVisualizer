"""Intervals: the statement every interval carries, and its validation.

An interval is drawn by a ``band`` or ``errorbar`` mark, or by ``y_lower`` /
``y_upper`` on a line, scatter or vertical bar mark. It is a claim about
uncertainty, so it must say what it is (:func:`interval_label`, e.g. "95 %
prediction interval"); :func:`check_intervals` reports an interval that does
not, and bounds that cannot be what they claim (lower above upper, a unit
other than the value's). Nothing here computes or adjusts a bound.
"""

from __future__ import annotations

from typing import Any

from ._common import is_number, ptr, suggest
from .data import Column, ColumnSchema, DatasetRef, DataSnapshot
from .marks import (
    INTERVAL_CHANNELS,
    INTERVAL_KINDS,
    INTERVAL_MARKS,
    LEVEL_KINDS,
    MULTIPLIER_KINDS,
    Mark,
)

_KIND_TEXT = {
    "confidence": "confidence interval",
    "credible": "credible interval",
    "prediction": "prediction interval",
    "quantile": "quantile interval",
}
_SPREAD_TEXT = {"standard-error": "standard error", "standard-deviation": "standard deviation"}
_NUMERIC = ("float", "int")


def _number(v: float, decimal: str) -> str:
    text = f"{v:.6g}"
    return text.replace(".", decimal) if decimal != "." else text


def interval_label(kind: str, level: float | None = None, multiplier: float | None = None,
                   decimal: str = ".") -> str:
    """What an interval is, in words: ``"95 % prediction interval"``,
    ``"min-max range"``, ``"±1 standard error"``. ``decimal`` is the decimal
    separator (``","`` writes ``"97,5 %"``)."""
    if kind in LEVEL_KINDS:
        return f"{_number(round(level * 100, 6), decimal)} % {_KIND_TEXT[kind]}"
    if kind in MULTIPLIER_KINDS:
        noun = _SPREAD_TEXT[kind]
        return f"±{_number(multiplier, decimal)} {noun}{'' if multiplier == 1 else 's'}"
    return "min-max range"


def mark_interval_label(m: Mark, decimal: str = ".") -> str | None:
    """:func:`interval_label` of a mark's interval, or None if it has none."""
    if not has_interval(m):
        return None
    return interval_label(m.interval_kind, m.interval_level, m.interval_multiplier, decimal)


def has_interval(m: Any) -> bool:
    return getattr(m, "type", None) in INTERVAL_MARKS and isinstance(m.encoding, dict) and (
        m.type in ("band", "errorbar") or any(ch in m.encoding for ch in INTERVAL_CHANNELS))


def check_intervals(c: Any, p: str, m: Mark) -> None:
    """Report problems with layer ``m``'s interval (``c`` is the validator's
    context: ``add``, ``spec``, ``datasets``, ``scale``, ``units``,
    ``bad_columns``)."""
    if m.type not in INTERVAL_MARKS:
        return
    enc = m.encoding
    present = [ch for ch in INTERVAL_CHANNELS if ch in enc]
    if not has_interval(m):
        for key in ("interval_kind", "interval_level", "interval_multiplier"):
            if getattr(m, key) is not None:
                c.add(f"{p}/{key}", "interval_without_bounds",
                      f"{key} is set but the layer draws no interval; bind y_lower and y_upper "
                      f"to the bound columns, or remove {key}.")
        return
    if m.type in ("line", "scatter", "bar"):
        for ch in INTERVAL_CHANNELS:
            if ch not in enc:
                c.add(f"{p}/encoding/{ch}", "missing_channel",
                      f"an interval needs both bounds; {present[0]!r} is bound, add \"{ch}\": "
                      "{\"field\": \"<column>\"} (a one-sided bound is not an interval).")
        cs = c.spec.coordinate_system
        if cs != "cartesian2d":
            c.add(f"{p}/encoding/{present[0]}", "unsupported_channel",
                  f"intervals are drawn in cartesian2d only, not in {cs}; remove y_lower/y_upper "
                  "or draw the figure in 2D.")
        if m.type == "bar" and getattr(m, "orientation", None) == "horizontal":
            c.add(f"{p}/encoding/{present[0]}", "unsupported_channel",
                  "y_lower/y_upper bound the y values, but a horizontal bar's values are on x; "
                  "intervals are supported on vertical bars only.")
    _check_metadata(c, p, m)
    if len(present) == 2:
        _check_bounds(c, p, m)


def metadata_problems(kind: Any, level: Any, multiplier: Any) -> list[tuple[str, str, str]]:
    """``(field, code, message)`` for each problem with an interval's stated
    kind / level / multiplier (``field`` is ``"kind"``, ``"level"`` or
    ``"multiplier"``). Shared by :func:`validate` and the templates."""
    out: list[tuple[str, str, str]] = []
    if kind is None:
        return [("kind", "missing_interval_kind",
                 "an interval must say what it is; set its kind to one of "
                 f"{', '.join(INTERVAL_KINDS)} (and its level, e.g. 0.95, for "
                 f"{', '.join(LEVEL_KINDS)}).")]
    if not isinstance(kind, str) or kind not in INTERVAL_KINDS:
        return [("kind", "invalid_interval_kind",
                 f"interval kind {kind!r} is unknown.{suggest(str(kind), INTERVAL_KINDS)} Use one "
                 f"of {', '.join(INTERVAL_KINDS)}.")]
    if kind in LEVEL_KINDS:
        if level is None:
            out.append(("level", "missing_interval_level",
                        f"a {kind} interval needs its coverage level, as a fraction: e.g. 0.95 "
                        "for a 95 % interval."))
        elif not is_number(level) or not 0 < level < 1:
            out.append(("level", "invalid_interval_level",
                        f"interval level {level!r} is not a probability strictly between 0 and "
                        "1; give it as a fraction (0.95 for a 95 % interval)."))
    elif level is not None:
        out.append(("level", "unexpected_interval_level",
                    f"a {kind} interval has no coverage level; remove the level"
                    + (" and give the multiplier (e.g. 1 for +-1 SE)." if kind in MULTIPLIER_KINDS
                       else ", or use kind 'quantile' with a level if that is what the bounds "
                       "are.")))
    if kind in MULTIPLIER_KINDS:
        if multiplier is None:
            out.append(("multiplier", "missing_interval_multiplier",
                        f"a {kind} bar must say how many {_SPREAD_TEXT[kind]}s it spans on each "
                        "side; set the multiplier (e.g. 1, or 1.96)."))
        elif not is_number(multiplier) or multiplier <= 0:
            out.append(("multiplier", "invalid_interval_multiplier",
                        f"the interval multiplier must be a number > 0, got {multiplier!r}."))
    elif multiplier is not None:
        out.append(("multiplier", "unexpected_interval_multiplier",
                    f"a multiplier applies to standard-error/-deviation bars only, not to a "
                    f"{kind} interval; remove it."))
    return out


def _check_metadata(c: Any, p: str, m: Mark) -> None:
    for fld, code, message in metadata_problems(m.interval_kind, m.interval_level,
                                                m.interval_multiplier):
        c.add(f"{p}/interval_{fld}", code, message)


def _column(ds: Any, enc: Any) -> Any:
    name = getattr(enc, "field", enc)
    if isinstance(ds, (DataSnapshot, DatasetRef)) and isinstance(ds.columns, dict):
        return ds.columns.get(name)
    return None


def _check_bounds(c: Any, p: str, m: Mark) -> None:
    ds = c.datasets.get(m.data) if isinstance(m.data, str) else None
    cols = {ch: _column(ds, m.encoding.get(ch)) for ch in ("y", "y_lower", "y_upper")}
    if any(not isinstance(col, (Column, ColumnSchema)) or id(col) in c.bad_columns
           for ch, col in cols.items() if ch != "y" or "y" in m.encoding):
        return  # unknown column / bad type: already reported by the binding checks
    lo, hi, val = cols["y_lower"], cols["y_upper"], cols["y"]
    ok = True
    for ch, col in (("y", val), ("y_lower", lo), ("y_upper", hi)):
        if col is not None and col.dtype not in (None,) + _NUMERIC:
            c.add(f"{p}/encoding/{ch}", "interval_not_numeric",
                  f"column {m.encoding[ch].field!r} is {col.dtype}; an interval needs numeric "
                  "bounds on a numeric value axis.")
            ok = False
    ref = val if val is not None else lo
    ref_ch = "y" if val is not None else "y_lower"
    for ch, col in (("y_lower", lo), ("y_upper", hi)):
        if col is not ref and col.unit != ref.unit:
            c.add(f"{p}/encoding/{ch}", "interval_unit_mismatch",
                  f"bound column {m.encoding[ch].field!r} is in {col.unit!r} but "
                  f"{m.encoding[ref_ch].field!r} is in {ref.unit!r}; an interval's bounds are "
                  "in the value's unit. Declare the same unit (nothing is converted).")
            ok = False
    if ok and val is None and lo.unit is not None:
        c.units["y"].append((f"{p}/encoding/y_lower", lo.unit))  # the axis' unit, for conflicts
    if c.scale("y") == "log":
        for ch, col in (("y_lower", lo), ("y_upper", hi)):
            if isinstance(col, Column) and col.dtype in _NUMERIC and any(
                    v <= 0 for v in col.non_null()):
                c.add(f"{p}/encoding/{ch}", "log_scale_nonpositive",
                      f"axis y is log-scaled but bound column {m.encoding[ch].field!r} has "
                      "values <= 0; they cannot be drawn. Use scale 'linear' or 'symlog'.")
    if ok and isinstance(lo, Column) and isinstance(hi, Column):
        _check_rows(c, m, lo, hi, val if isinstance(val, Column) else None)


def _check_rows(c: Any, m: Mark, lo: Column, hi: Column, val: Column | None) -> None:
    lname, uname = m.encoding["y_lower"].field, m.encoding["y_upper"].field
    inverted = [i for i, (a, b) in enumerate(zip(lo.values, hi.values))
                if is_number(a) and is_number(b) and a > b]
    if inverted:
        i = inverted[0]
        c.add(ptr("datasets", m.data, "columns", lname, "values", str(i)), "interval_inverted",
              f"row {i}: lower bound {lo.values[i]!r} ({lname!r}) is above upper bound "
              f"{hi.values[i]!r} ({uname!r}); {len(inverted)} row(s) are inverted. Bounds are "
              "drawn as given, so fix them upstream (swapped columns?).")
    if val is None:
        return
    outside = [i for i, (v, a, b) in enumerate(zip(val.values, lo.values, hi.values))
               if is_number(v) and is_number(a) and is_number(b) and a <= b and not a <= v <= b]
    if outside:
        i = outside[0]
        name = m.encoding["y"].field
        c.add(ptr("datasets", m.data, "columns", name, "values", str(i)),
              "value_outside_interval",
              f"row {i}: {name!r} = {val.values[i]!r} lies outside its interval "
              f"[{lo.values[i]!r}, {hi.values[i]!r}] ({len(outside)} row(s)); check that the "
              "bounds belong to this value.", "warning")
