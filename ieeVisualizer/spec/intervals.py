"""Intervals: the statement every interval carries, and its validation.

An interval is drawn by a ``band`` or ``errorbar`` mark, or by ``y_lower`` /
``y_upper`` on a line, scatter or vertical bar mark. It is a claim about
uncertainty, so it must say what it is (:func:`interval_label`, e.g. "95 %
prediction interval"); :func:`check_intervals` reports an interval that does
not, and bounds that cannot be what they claim (lower above upper, a unit
other than the value's). Nothing here computes or adjusts a bound.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
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


#: Between a number and "%" (and after "±"): a no-break space, so a caption
#: or legend is never wrapped between "95" and "%".
NBSP = "\u00a0"
#: Most decimals a number in a label is written with (beyond float noise).
MAX_PLACES = 12


def _shown(v: float) -> tuple[Decimal, Decimal]:
    """(exact decimal of ``v``, what a label shows): exact when it has at
    most 4 decimals, else rounded to 4 -- but never onto a whole number it is
    not (99.99999 % does not read as 100 %, a multiplier of 1.00001 as 1):
    more decimals are used instead, up to :data:`MAX_PLACES`."""
    exact = Decimal(repr(float(v)))
    places = 4
    while True:
        shown = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
        if exact == shown or places >= MAX_PLACES or shown != shown.to_integral_value():
            return exact, shown
        places += 1


def _number(v: float, decimal: str = ".") -> str:
    """``v`` as :func:`_shown` writes it, plain decimal (never scientific)."""
    text = format(_shown(v)[1].normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", decimal) if decimal != "." else text


def _percent_value(fraction: float) -> float:
    return float(Decimal(repr(float(fraction))) * 100)


def _percent(fraction: float, decimal: str) -> str:
    return _number(_percent_value(fraction), decimal)


def _pct(fraction: float, decimal: str) -> str:
    """A quantile in percentile notation: 0.025 -> ``P2.5``."""
    return f"P{_percent(fraction, decimal)}"


def indistinct_percent(fraction: float) -> bool:
    """A fraction in (0, 1) whose percentage cannot be written distinctly
    from 0 % or 100 % (within :data:`MAX_PLACES` decimals)."""
    exact, shown = _shown(_percent_value(fraction))
    return exact != shown and shown in (Decimal(0), Decimal(100))


def interval_label(kind: str, level: float | None = None, multiplier: float | None = None,
                   decimal: str = ".", quantiles: list[float] | None = None) -> str:
    """What an interval is, in words: ``"95 % prediction interval"``,
    ``"80 % central interval (P10–P90)"`` (a quantile interval with a level:
    equal-tailed), ``"quantile interval P5–P75"``, ``"min-max range"``,
    ``"±1 standard error"``. ``decimal`` is the decimal separator (``","``
    writes ``"97,5 %"``); "%" follows a no-break space. Numbers are written
    exactly up to 4 decimals, rounded beyond that but never onto a whole
    number they are not (levels that would need more than
    :data:`MAX_PLACES` decimals for that are refused by validation)."""
    pct = f"{NBSP}%"
    if kind == "quantile":
        if quantiles is not None:
            lo, hi = quantiles
            return f"quantile interval {_pct(lo, decimal)}–{_pct(hi, decimal)}"
        tail = (Decimal(1) - Decimal(repr(float(level)))) / 2  # equal-tailed (central)
        return (f"{_percent(level, decimal)}{pct} central interval "
                f"({_pct(float(tail), decimal)}–{_pct(float(1 - tail), decimal)})")
    if kind in LEVEL_KINDS:
        return f"{_percent(level, decimal)}{pct} {_KIND_TEXT[kind]}"
    if kind in MULTIPLIER_KINDS:
        text = _number(multiplier, decimal)
        return f"±{text} {_SPREAD_TEXT[kind]}{'' if text == '1' else 's'}"
    return "min-max range"


def mark_interval_label(m: Mark, decimal: str = ".") -> str | None:
    """:func:`interval_label` of a mark's interval, or None if it has none."""
    if not has_interval(m):
        return None
    return interval_label(m.interval_kind, m.interval_level, m.interval_multiplier, decimal,
                          getattr(m, "interval_quantiles", None))


#: Relative difference of the two half-widths above which a ±k SE/SD bar
#: is reported as not symmetric (float rounding stays well below it).
SYMMETRY_TOLERANCE = 1e-6


def asymmetric_rows(values: list[Any], lower: list[Any], upper: list[Any]) -> list[int]:
    """Rows whose bounds are not ``value ± the same half-width`` (all three
    numbers present). A "±k standard error" bar claims that symmetry."""
    out = []
    for i, (v, a, b) in enumerate(zip(values, lower, upper)):
        if is_number(v) and is_number(a) and is_number(b):
            below, above = v - a, b - v
            if abs(below - above) > SYMMETRY_TOLERANCE * max(abs(below), abs(above), 1e-300):
                out.append(i)
    return out


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
        for key in ("interval_kind", "interval_level", "interval_multiplier",
                    "interval_quantiles"):
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


def metadata_problems(kind: Any, level: Any, multiplier: Any,
                      quantiles: Any = None) -> list[tuple[str, str, str]]:
    """``(field, code, message)`` for each problem with an interval's stated
    kind / level / multiplier / quantiles (``field`` is ``"kind"``,
    ``"level"``, ``"multiplier"`` or ``"quantiles"``). Shared by
    :func:`validate` and the templates."""
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
    if quantiles is not None:
        if kind != "quantile":
            out.append(("quantiles", "unexpected_interval_quantiles",
                        f"explicit quantiles describe a quantile interval, not a {kind} one; "
                        "remove them or use kind 'quantile'."))
        elif level is not None:
            out.append(("quantiles", "conflicting_interval_level",
                        "give either a level (a central interval) or explicit quantiles "
                        "[lower, upper], not both."))
        elif not (isinstance(quantiles, (list, tuple)) and len(quantiles) == 2
                  and all(is_number(q) for q in quantiles)
                  and 0 < quantiles[0] < quantiles[1] < 1):
            out.append(("quantiles", "invalid_interval_quantiles",
                        f"interval quantiles must be [lower, upper] with 0 < lower < upper < 1 "
                        f"(e.g. [0.05, 0.75]), got {quantiles!r}."))
        elif any(indistinct_percent(q) for q in quantiles):
            out.append(("quantiles", "invalid_interval_quantiles",
                        f"interval quantiles {quantiles!r} cannot be written distinctly from "
                        "P0/P100; a figure would claim the extreme. Round them upstream."))
    if kind in LEVEL_KINDS and not (kind == "quantile" and quantiles is not None):
        if level is None:
            out.append(("level", "missing_interval_level",
                        f"a {kind} interval needs its coverage level, as a fraction: e.g. 0.95 "
                        "for a 95 % interval"
                        + (" (central), or explicit quantiles [lower, upper]."
                           if kind == "quantile" else ".")))
        elif not is_number(level) or not 0 < level < 1:
            out.append(("level", "invalid_interval_level",
                        f"interval level {level!r} is not a probability strictly between 0 and "
                        "1; give it as a fraction (0.95 for a 95 % interval)."))
        elif indistinct_percent(level) or (kind == "quantile" and indistinct_percent(
                float((Decimal(1) - Decimal(repr(float(level)))) / 2))):
            out.append(("level", "invalid_interval_level",
                        f"interval level {level!r} cannot be written distinctly from 0 % or "
                        "100 % (or its tails from P0/P100); a figure would claim the extreme. "
                        "Round it upstream."))
    elif level is not None and kind not in LEVEL_KINDS:
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
                                                m.interval_multiplier,
                                                getattr(m, "interval_quantiles", None)):
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
    if m.interval_kind in MULTIPLIER_KINDS:
        asym = asymmetric_rows(val.values, lo.values, hi.values)
        if asym:
            i = asym[0]
            name = m.encoding["y"].field
            c.add(ptr("datasets", m.data, "columns", uname, "values", str(i)),
                  "interval_not_symmetric",
                  f"row {i}: {name!r} = {val.values[i]!r} is not midway between "
                  f"{lo.values[i]!r} and {hi.values[i]!r} ({len(asym)} row(s)), but a "
                  f"{m.interval_kind} bar claims value ± the same half-width. The bounds are "
                  "drawn as given and the legend says they are not symmetric; check how they "
                  "were computed, or state the interval's real kind.", "warning")
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
