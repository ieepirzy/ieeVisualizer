"""Built-in ``distribution`` template: named probability distributions drawn
from their closed forms.

Families: normal, log-normal, beta, gamma, Student t (continuous; PDF drawn
as a line) and Poisson, binomial (discrete; PMF drawn as bars, never a
line). ``view="cdf"`` draws the CDF instead (bars for a discrete family).
Several distributions of one kind (all continuous or all discrete) share
the axes for comparison.

* **Shaded interval** (``interval``): ``{"kind": "quantile", "level": 0.9}``
  is the central 90 % of each distribution, labelled "central 90% of the
  distribution (P5–P95)"; ``{"kind": "quantile", "quantiles": [0.05, 0.75]}``
  a non-central one; ``credible`` / ``prediction`` with a level when the
  distribution is a posterior / predictive one. Kind and level are checked
  as the spec checks intervals (``spec.intervals.metadata_problems``). On a
  PDF the area under each curve between the two quantiles is filled in that
  curve's colour (its area is the stated probability); on a CDF or PMF the
  span is a reference band. A discrete interval cannot cover exactly the
  stated level, so its label also says what it covers ("3–9 covers 92.4%").
* **Quantile markers** (``quantile_markers``): points on each curve at the
  given quantiles; their values are written in the caption.
* Every number the template writes uses the ``theme`` parameter's locale.

Nothing is sampled or estimated: values come from the formulas in
:mod:`._distributions` and are recorded with the formulas, parameters, the
x range and the grid in ``metadata["template"]``. The dataset provenance and
the caption say the curves are theoretical, not data.
"""

from __future__ import annotations

import math
from typing import Any

from ..spec import AreaMark, Axis, BarMark, DataSnapshot, FigureSpec, LineMark, ScatterMark
from ..spec._common import ptr, suggest
from ..spec.data import Column, Provenance
from ..spec.figure import ReferenceBand
from ..spec.intervals import (
    distribution_interval_label,
    indistinct_percent,
    metadata_problems,
    percentile_text,
)
from ..themes import format_percent
from ..themes.locale import localize_decimal
from ._business import THEME_PARAM, check_theme, locale_for
from ._distributions import FAMILIES, Dist, NumericalLimit, param_problem, resolved_params
from .base import ComponentTemplate, Issues, Parameter, param_path

MAX_DISTRIBUTIONS = 6
MAX_MARKERS = 9
#: Most integers a discrete distribution is drawn at (one bar each).
MAX_SUPPORT = 200
#: Default x range: these quantiles of every distribution (or the support's end).
RANGE_TAIL = 0.001
#: A distribution of which the drawn x range holds less than this share (an
#: explicit x_range that cuts it) gets a caption note saying how much it holds.
MIN_SHOWN_SHARE = 0.998 - 1e-9
ITEM_KEYS = ("family", "params", "label")
INTERVAL_KEYS = ("kind", "level", "quantiles")
#: Kinds an interval read off a distribution can be.
DIST_INTERVAL_KINDS = ("quantile", "credible", "prediction")
NUMERICS = ("PDF/PMF: closed forms (formula); CDF: erfc, regularized incomplete beta and gamma "
            "functions (continued fractions, bounded iterations); quantiles: bisection on the "
            "CDF (discrete: smallest k with F(k) >= p). Values stored to 10 significant digits.")


def store(v: float) -> float:
    """A computed value as stored in the spec: 10 significant digits (the
    last bits of libm results differ between machines), never ``-0.0``."""
    out = float(f"{v:.10g}")
    return 0.0 if out == 0 else out


def number_text(v: float, loc: Any, sig: int = 4) -> str:
    """``v`` to ``sig`` significant digits, plain decimal, in ``loc``."""
    if v == 0:
        return "0"
    places = max(0, sig - 1 - math.floor(math.log10(abs(v))))
    text = f"{v:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return localize_decimal(text, loc)


def default_label(family: Any, params: dict[str, float], loc: Any) -> str:
    sep = "; " if loc.decimal == "," else ", "
    inner = sep.join(f"{p.symbol} = {number_text(params[p.name], loc)}" for p in family.params)
    return f"{family.title}({inner})"


class DistributionTemplate(ComponentTemplate):
    name = "distribution"
    version = "1.0.0"
    title = "Probability distributions"
    description = (
        "PDF/PMF or CDF of named distributions (normal, log-normal, beta, gamma, Student t, "
        "Poisson, binomial) from their closed forms, compared on one axis, with an optional "
        "shaded interval labelled with its kind and level and optional quantile markers."
    )
    data_required = False
    parameters = (
        Parameter("distributions", "object_list", "The distributions: [{family, params, "
                  "label?}]. family: normal {mu, sigma}, lognormal {mu, sigma} (of log x), "
                  "beta {alpha, beta}, gamma {shape, scale}, student_t {df, loc=0, scale=1}, "
                  "poisson {lambda}, binomial {n, p}. All continuous or all discrete.",
                  required=True),
        Parameter("view", "string", "'pdf' (density; the PMF for discrete families) or 'cdf'.",
                  default="pdf", allowed=("pdf", "cdf")),
        Parameter("interval", "object", "Shaded interval of each distribution: {kind, level?, "
                  "quantiles?}; kind quantile (level = central share, or quantiles [lower, "
                  "upper]), credible or prediction (with a level)."),
        Parameter("quantile_markers", "number_list", "Quantiles to mark on each curve, as "
                  "probabilities (e.g. [0.05, 0.5, 0.95]); their values go in the caption."),
        Parameter("x_range", "range", "x range drawn; default: the 0.1 % to 99.9 % quantiles "
                  "(or the support's end) of every distribution."),
        Parameter("samples", "integer", "Points of each continuous curve (21 to 2001).",
                  default=201),
        Parameter("x_label", "string", "X-axis label.", default="x"),
        Parameter("x_unit", "string", "Unit of x (also gives the density's unit, 1/unit)."),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption; the template appends what the curves "
                  "are and any quantile values."),
        THEME_PARAM,
    )
    notes = (
        "Theoretical curves from closed-form formulas: the provenance and caption say so; "
        "nothing is sampled or estimated.",
        "Discrete families are drawn as bars, never as a line; a discrete interval states the "
        "share it actually covers.",
        "An interval states its kind and level ('central 90% of the distribution (P5–P95)').",
    )

    # ----------------------------------------------------------------- checks
    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        check_theme(p, issues)
        dists = self._check_items(p["distributions"], issues)
        if not (21 <= p["samples"] <= 2001):
            issues.error(param_path("samples"), "invalid_parameter",
                         f"samples must be between 21 and 2001, got {p['samples']}.")
        self._check_interval(p["interval"], issues)
        self._check_markers(p["quantile_markers"], issues)
        if issues.errors or not dists:
            return
        kinds = {d.discrete for d in dists}
        if len(kinds) > 1:
            issues.error(param_path("distributions"), "conflicting_parameters",
                         "continuous and discrete distributions cannot share an axis (a "
                         "density is not a probability); draw them in separate figures.")
            return
        loc = locale_for(p["theme"]) if not issues.errors else None
        labels = [i.get("label") or default_label(d.family, d.p, loc)
                  for i, d in zip(p["distributions"], dists)] if loc else []
        if len(set(labels)) != len(labels):
            issues.error(param_path("distributions"), "invalid_parameter",
                         f"two distributions would both be called {labels!r} in the legend; "
                         "give them distinct labels.")
        several, discrete = len(dists) > 1, dists[0].discrete
        if several and p["interval"] is not None and (discrete or p["view"] == "cdf"):
            issues.error(param_path("interval"), "conflicting_parameters",
                         "on a CDF or a discrete distribution an interval is a shaded span "
                         "that could not be told apart between several distributions; shade "
                         "it for one distribution per figure (or use the PDF view).")
        if several and discrete and p["quantile_markers"] is not None:
            issues.error(param_path("quantile_markers"), "conflicting_parameters",
                         "quantile markers on several discrete distributions would sit "
                         "between their dodged bars; mark one distribution per figure.")
        try:
            lo, hi = self._x_range(dists, p)
        except NumericalLimit as e:
            issues.error(param_path("distributions"), "invalid_parameter",
                         f"the parameters are too extreme to evaluate in bounded time ({e}).")
            return
        if discrete:
            count = math.floor(hi) - math.ceil(lo) + 1
            if count < 1:
                issues.error(param_path("x_range"), "invalid_parameter",
                             f"x_range {[lo, hi]} contains no whole number; a discrete "
                             "distribution has mass only at whole numbers.")
            elif count > MAX_SUPPORT:
                issues.error(param_path("x_range") if p["x_range"] else
                             param_path("distributions"), "invalid_parameter",
                             f"{count} values from {math.ceil(lo)} to {math.floor(hi)} would "
                             f"be drawn as bars (at most {MAX_SUPPORT}); use view 'cdf' "
                             "with a continuous approximation upstream, or a narrower "
                             "x_range.")

    def _check_items(self, items: Any, issues: Issues) -> list[Dist]:
        if not isinstance(items, list):
            return []
        if len(items) > MAX_DISTRIBUTIONS:
            issues.error(param_path("distributions"), "invalid_parameter",
                         f"at most {MAX_DISTRIBUTIONS} distributions fit one legible figure; "
                         f"got {len(items)}.")
            return []
        out = []
        for k, item in enumerate(items):
            base = ptr("params", "distributions", str(k))
            for key in sorted(set(item) - set(ITEM_KEYS)):
                issues.error(f"{base}/{key}", "invalid_parameter",
                             f"distribution {k} has no key {key!r}.{suggest(key, ITEM_KEYS)} "
                             f"Keys: {', '.join(ITEM_KEYS)}.")
            label = item.get("label")
            if label is not None and (not isinstance(label, str) or not label.strip()):
                issues.error(f"{base}/label", "invalid_parameter",
                             f"distribution {k}'s label must be a non-empty string.")
            fam = item.get("family")
            if fam is None:
                issues.error(f"{base}/family", "missing_parameter",
                             f"distribution {k} needs a family: {', '.join(FAMILIES)}.")
                continue
            family = FAMILIES.get(fam) if isinstance(fam, str) else None
            if family is None:
                issues.error(f"{base}/family", "invalid_parameter",
                             f"unknown family {fam!r}.{suggest(str(fam), FAMILIES)} Families: "
                             f"{', '.join(FAMILIES)}.")
                continue
            given = item.get("params", {})
            if not isinstance(given, dict):
                issues.error(f"{base}/params", "invalid_parameter",
                             f"distribution {k}'s params must be an object, got {given!r}.")
                continue
            names = [q.name for q in family.params]
            ok = True
            for key in sorted(set(given) - set(names)):
                issues.error(f"{base}/params/{key}", "unknown_parameter",
                             f"{family.title} has no parameter {key!r}.{suggest(key, names)} "
                             f"Parameters: {', '.join(names)}.")
                ok = False
            for q in family.params:
                v = given.get(q.name, q.default)
                if v is None:
                    issues.error(f"{base}/params/{q.name}", "missing_parameter",
                                 f"{family.title} needs {q.name!r} ({q.symbol}).")
                    ok = False
                    continue
                why = param_problem(family, q.name, v)
                if why:
                    issues.error(f"{base}/params/{q.name}", "invalid_parameter", why)
                    ok = False
            if ok:
                out.append(Dist(family, resolved_params(family, given)))
        return out if len(out) == len(items) else []

    @staticmethod
    def _check_interval(iv: Any, issues: Issues) -> None:
        if iv is None:
            return
        for key in sorted(set(iv) - set(INTERVAL_KEYS)):
            issues.error(param_path("interval") + f"/{key}", "invalid_parameter",
                         f"interval has no key {key!r}.{suggest(key, INTERVAL_KEYS)} Keys: "
                         f"{', '.join(INTERVAL_KEYS)}.")
        kind = iv.get("kind")
        if isinstance(kind, str) and kind not in DIST_INTERVAL_KINDS and kind in (
                "confidence", "min-max", "standard-error", "standard-deviation"):
            issues.error(param_path("interval") + "/kind", "invalid_interval_kind",
                         f"a {kind} interval describes an estimate or a sample, not a share "
                         "of a distribution; use kind 'quantile' (or 'credible'/'prediction' "
                         "for a posterior/predictive distribution).")
            return
        for fld, code, message in metadata_problems(kind, iv.get("level"), None,
                                                    iv.get("quantiles")):
            issues.error(param_path("interval") + f"/{fld}", code, message)

    @staticmethod
    def _check_markers(qs: Any, issues: Issues) -> None:
        if qs is None:
            return
        if len(qs) > MAX_MARKERS:
            issues.error(param_path("quantile_markers"), "invalid_parameter",
                         f"at most {MAX_MARKERS} quantile markers; got {len(qs)}.")
        texts = set()
        for k, q in enumerate(qs):
            path = ptr("params", "quantile_markers", str(k))
            if not 0 < q < 1:
                issues.error(path, "invalid_parameter",
                             f"a quantile is a probability strictly between 0 and 1 (0.5 for "
                             f"the median), got {q!r}.")
            elif indistinct_percent(q) or percentile_text(q) in texts:
                issues.error(path, "invalid_parameter",
                             f"quantile {q!r} cannot be written distinctly from P0/P100 or "
                             "from another marker; round it upstream.")
            else:
                texts.add(percentile_text(q))

    # ------------------------------------------------------------------ build
    @staticmethod
    def _x_range(dists: list[Dist], p: dict[str, Any]) -> tuple[float, float]:
        if p["x_range"] is not None:
            return tuple(p["x_range"])
        los, his = [], []
        for d in dists:
            f = d.family
            los.append(f.lower if f.lower is not None and not d.discrete
                       else d.quantile(RANGE_TAIL))
            his.append(f.upper if f.upper is not None else d.quantile(1 - RANGE_TAIL))
        lo, hi = min(los), max(his)
        if hi <= lo:  # a point mass (binomial with p = 0 or 1)
            lo, hi = lo - 1, hi + 1
        return lo, hi

    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        loc = locale_for(p["theme"])
        items = p["distributions"]
        dists = [Dist(FAMILIES[i["family"]], resolved_params(FAMILIES[i["family"]],
                                                             i.get("params", {})))
                 for i in items]
        labels = [i.get("label") or default_label(d.family, d.p, loc)
                  for i, d in zip(items, dists)]
        lo, hi = self._x_range(dists, p)
        discrete, cdf = dists[0].discrete, p["view"] == "cdf"
        spec = FigureSpec(title=p["title"], theme=p["theme"])
        meta_d: list[dict[str, Any]] = []
        cut: list[float] = []  # y limit candidates when a density is unbounded
        notes: list[str] = []
        for k, (d, label) in enumerate(zip(dists, labels)):
            name = f"dist_{k}"
            xs = (list(range(math.ceil(lo), math.floor(hi) + 1)) if discrete
                  else [lo + (hi - lo) * j / (p["samples"] - 1) for j in range(p["samples"])])
            unbounded = [] if cdf or discrete else [x for x in d.unbounded_at() if lo <= x <= hi]
            if unbounded:  # approach an infinite end geometrically so the curve leaves the plot
                near = [e + (1 if e == lo else -1) * (hi - lo) * 10.0 ** -k
                        for e in unbounded for k in range(3, 10)]
                xs = sorted(set(x for x in xs if x not in unbounded) | set(near))
            ys = [store(d.cdf(x) if cdf else d.density(x)) for x in xs]
            ycol = "cumulative" if cdf else ("probability" if discrete else "density")
            spec.datasets[name] = DataSnapshot(
                columns={"x": Column([store(x) for x in xs] if not discrete else xs,
                                     dtype="int" if discrete else "float", unit=p["x_unit"]),
                         ycol: Column(ys, dtype="float", unit=self._y_unit(p, discrete, cdf))},
                provenance=Provenance(
                    source="ieeVisualizer.templates.distribution",
                    notes=f"{d.family.title} distribution computed from its closed-form "
                          f"{'CDF' if cdf else ('PMF' if discrete else 'PDF')}; theoretical, "
                          "not a measurement.",
                    extra={"family": d.family.name, "params": dict(d.p)}))
            if discrete:
                spec.layers.append(BarMark(data=name, encoding={"x": "x", "y": ycol}, name=label))
            else:
                spec.layers.append(LineMark(data=name, encoding={"x": "x", "y": ycol},
                                            name=label))
            entry: dict[str, Any] = {"label": label, "family": d.family.name,
                                     "params": dict(d.p), "formula": d.family.formula,
                                     "points": len(xs)}
            if unbounded:
                entry["density_unbounded_at"] = unbounded
                inner = [d.density(x) for x in xs if d.quantile(0.1) <= x <= d.quantile(0.9)]
                cut.append(2.5 * max(inner or [1.0]))
                where = " and ".join(number_text(x, loc) for x in unbounded)
                notes.append(f"{label}: the density is unbounded at x = {where}; the curve runs "
                             "off the top of the plot there.")
            cover = d.cdf(hi) - (d.cdf(math.ceil(lo) - 1) if discrete else d.cdf(lo))
            entry["drawn_share"] = store(cover)
            if cover < MIN_SHOWN_SHARE:
                notes.append(f"{label}: the x range drawn holds "
                             f"{format_percent(cover * 100, loc)} of its probability.")
            meta_d.append(entry)
        interval_meta = self._interval(spec, p, dists, labels, loc, discrete, cdf, meta_d)
        marker_lines = self._markers(spec, p, dists, labels, loc, discrete, cdf, meta_d,
                                     hi - lo)
        spec.axes = {"x": Axis(label=p["x_label"], unit=p["x_unit"]),
                     "y": self._y_axis(p, discrete, cdf, cut)}
        what = "distribution" if len(dists) == 1 else "distributions"
        lines = ([p["caption"]] if p["caption"] else []) + [
            f"Theoretical {what} from closed-form formulas, not data."] + notes + marker_lines
        spec.caption = "\n".join(lines)
        spec.metadata["template"] = {
            "distributions": meta_d, "view": p["view"],
            "x_range": [store(lo), store(hi)], "numerics": NUMERICS,
            "label_locale": loc.tag}
        if interval_meta:
            spec.metadata["template"]["interval"] = interval_meta
        return spec

    @staticmethod
    def _y_unit(p: dict[str, Any], discrete: bool, cdf: bool) -> str | None:
        return f"1/{p['x_unit']}" if p["x_unit"] and not (discrete or cdf) else None

    def _y_axis(self, p: dict[str, Any], discrete: bool, cdf: bool, cut: list[float]) -> Axis:
        if cdf:
            return Axis(label="Cumulative probability", limits=[0.0, 1.0])
        if discrete:
            return Axis(label="Probability", limits=[0.0, None])
        return Axis(label="Density", unit=self._y_unit(p, discrete, cdf),
                    limits=[0.0, store(max(cut)) if cut else None])

    def _interval(self, spec: FigureSpec, p: dict[str, Any], dists: list[Dist],
                  labels: list[str], loc: Any, discrete: bool, cdf: bool,
                  meta_d: list[dict[str, Any]]) -> dict[str, Any] | None:
        iv = p["interval"]
        if iv is None:
            return None
        kind, level, qs = iv["kind"], iv.get("level"), iv.get("quantiles")
        plo, phi = (qs[0], qs[1]) if qs is not None else ((1 - level) / 2, (1 + level) / 2)
        text = distribution_interval_label(kind, level, qs, loc.decimal, loc.percent_sep)
        for k, (d, label) in enumerate(zip(dists, labels)):
            qlo, qhi = d.quantile(plo), d.quantile(phi)
            entry = {"lower": store(qlo), "upper": store(qhi)}
            name = text[0].upper() + text[1:] if len(dists) == 1 else f"{label}: {text}"
            if discrete:
                cover = d.cdf(qhi) - d.cdf(qlo - 1)
                entry["coverage"] = store(cover)
                name += (f"; {number_text(qlo, loc)}–{number_text(qhi, loc)} covers "
                         f"{format_percent(cover * 100, loc)}")
            meta_d[k]["interval"] = entry
            if discrete or cdf:
                half = 0.5 if discrete else 0.0
                spec.annotations.append(ReferenceBand(axis="x", start=store(qlo - half),
                                                      end=store(qhi + half), label=name))
                continue
            n = p["samples"]
            inside = [qlo + (qhi - qlo) * j / (n - 1) for j in range(n)]
            ys = [d.density(x) for x in inside]
            ys = [None if math.isinf(y) else store(y) for y in ys]
            dname = f"interval_{k}"
            spec.datasets[dname] = DataSnapshot(
                columns={"x": Column([store(x) for x in inside], dtype="float",
                                     unit=p["x_unit"]),
                         "density": Column(ys, dtype="float",
                                           unit=self._y_unit(p, discrete, cdf))},
                provenance=spec.datasets[f"dist_{k}"].provenance)
            # drawn before the curves so every line stays on top
            at = next(i for i, m in enumerate(spec.layers) if m.data == f"dist_{k}")
            spec.layers.insert(at, AreaMark(data=dname, encoding={"x": "x", "y": "density"},
                                            name=name, style={"color_of": label,
                                                              "opacity": 0.3}))
        out = {"kind": kind, "quantiles": [plo, phi], "label": text}
        if level is not None:
            out["level"] = level
        return out

    def _markers(self, spec: FigureSpec, p: dict[str, Any], dists: list[Dist],
                 labels: list[str], loc: Any, discrete: bool, cdf: bool,
                 meta_d: list[dict[str, Any]], width: float) -> list[str]:
        qs = p["quantile_markers"]
        if qs is None:
            return []
        qs = sorted(qs)
        names = ", ".join(percentile_text(q, loc.decimal) for q in qs)
        lines = []
        for k, (d, label) in enumerate(zip(dists, labels)):
            # bisection noise around 0 (|x| below 1e-12 of the range) is written as 0
            xq = [0.0 if abs(x) < 1e-12 * width else x for x in map(d.quantile, qs)]
            yq = [q if cdf else d.density(x) for q, x in zip(qs, xq)]
            meta_d[k]["quantiles"] = [{"p": q, "x": store(x)} for q, x in zip(qs, xq)]
            dname = f"quantiles_{k}"
            spec.datasets[dname] = DataSnapshot(
                columns={"x": Column([store(x) for x in xq], dtype="float", unit=p["x_unit"]),
                         "y": Column([store(y) for y in yq], dtype="float",
                                     unit=self._y_unit(p, discrete, cdf))},
                provenance=spec.datasets[f"dist_{k}"].provenance)
            mark_name = f"Quantiles {names}" if len(dists) == 1 else f"{label}: {names}"
            style = {"marker": "D"} if discrete else {"color_of": label, "marker": "D"}
            spec.layers.append(ScatterMark(data=dname, encoding={"x": "x", "y": "y"},
                                           name=mark_name, style=style,
                                           role="reference" if discrete else "observed"))
            unit = f" {p['x_unit']}" if p["x_unit"] else ""
            values = "; ".join(f"{percentile_text(q, loc.decimal)} = {number_text(x, loc)}{unit}"
                               for q, x in zip(qs, xq))
            lines.append(values + "." if len(dists) == 1 else f"{label}: {values}.")
        return lines

    def example(self) -> dict[str, Any]:
        return {"data": None, "params": {
            "distributions": [
                {"family": "lognormal", "params": {"mu": 1.0, "sigma": 0.45},
                 "label": "Model A (log-normal)"},
                {"family": "gamma", "params": {"shape": 5.0, "scale": 0.6},
                 "label": "Model B (gamma)"}],
            "interval": {"kind": "quantile", "level": 0.9},
            "quantile_markers": [0.5],
            "x_label": "Delivery time", "x_unit": "days",
            "title": "Delivery time under two candidate models",
            "caption": "Illustrative parameters, not fitted to data."}}
