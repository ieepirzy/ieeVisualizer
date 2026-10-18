"""Built-in ``sample_distribution`` template: the distribution of a sample.

``display``:

* ``"histogram"``: a density-scaled histogram plus, by default, a Gaussian
  kernel density estimate (KDE). The bandwidth is the claim a KDE makes, so
  its method and value are recorded in ``metadata["template"]["kde"]`` and
  written in the caption and the curve's legend entry: ``scott``
  (h = σ̂·n^(−1/5), as ``scipy.stats.gaussian_kde``), ``silverman``
  (h = 0.9·min(σ̂, IQR/1.34)·n^(−1/5), Silverman 1986 eq. 3.31) or
  ``fixed`` (``bandwidth``, in the sample's unit). Bins: ``bins`` equal
  widths over the sample's range, or by default the Freedman–Diaconis rule
  (width 2·IQR·n^(−1/3); Sturges when the IQR is 0), recorded likewise.
* ``"ecdf"``: the empirical CDF, an exact step function (a vertical riser
  at every sample value), optionally one per group (``group_column``) for
  comparing samples.
* ``"dotplot"``: a quantile dotplot (Kay et al. 2016): ``dots`` dots, each
  standing for 1/``dots`` of the sample, at the sample quantiles
  (i − 0.5)/``dots``, stacked in columns of a stated width.

Null values are not samples: they are dropped and counted
(``null_samples_dropped``). Sample quantiles are type 7 (linear
interpolation between order statistics, numpy's default). Computed values
are stored to 10 significant digits.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from ..spec import Axis, DataSnapshot, FigureSpec, HistogramMark, LineMark, ScatterMark
from ..spec.data import Column
from ..themes.locale import NBSP
from ._business import THEME_PARAM, check_theme, copy_rows, locale_for
from .base import ColumnRequirement, ComponentTemplate, Issues, Parameter, column_path, param_path
from .distribution import number_text, store

MAX_SAMPLES = 100_000
MAX_GROUPS = 6
MAX_BINS = 100
KDE_POINTS = 256
#: The KDE is drawn this many bandwidths beyond the smallest and largest sample.
KDE_REACH = 3.0
DISPLAYS = ("histogram", "ecdf", "dotplot")
_BW_TEXT = {"scott": "Scott's rule, σ̂·n^(−1/5)",
            "silverman": "Silverman's rule, 0.9·min(σ̂, IQR/1.34)·n^(−1/5)",
            "fixed": "fixed by the caller"}


def quantile7(xs: list[float], q: float) -> float:
    """Type 7 sample quantile of the sorted list ``xs``."""
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def _sd(xs: list[float]) -> float:
    m = math.fsum(xs) / len(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def bandwidth(xs: list[float], method: str, fixed: float | None) -> float:
    """The KDE bandwidth of the sorted sample ``xs`` (0 when undefined)."""
    if method == "fixed":
        return float(fixed)
    sd, n = _sd(xs), len(xs)
    if method == "scott":
        return sd * n ** -0.2
    iqr = quantile7(xs, 0.75) - quantile7(xs, 0.25)
    spread = min(sd, iqr / 1.34) if iqr > 0 else sd
    return 0.9 * spread * n ** -0.2


class SampleDistributionTemplate(ComponentTemplate):
    name = "sample_distribution"
    version = "1.0.0"
    title = "Distribution of a sample"
    description = (
        "Histogram with a kernel density estimate (bandwidth method and value recorded and "
        "stated), empirical CDF (one per group for comparisons) or quantile dotplot of a "
        "numeric sample."
    )
    parameters = (
        Parameter("value_column", "column", "Numeric column holding the sample (one value per "
                  "row; nulls are dropped and counted).", default="value"),
        Parameter("group_column", "column", "ECDF only: column splitting the sample into "
                  "groups drawn as separate curves."),
        Parameter("display", "string", "'histogram' (with KDE), 'ecdf' or 'dotplot'.",
                  default="histogram", allowed=DISPLAYS),
        Parameter("kde", "boolean", "Histogram: overlay a Gaussian KDE.", default=True),
        Parameter("bandwidth_method", "string", "KDE bandwidth: 'scott', 'silverman' or "
                  "'fixed' (then give bandwidth).", default="scott",
                  allowed=("scott", "silverman", "fixed")),
        Parameter("bandwidth", "number", "KDE bandwidth in the sample's unit, with "
                  "bandwidth_method 'fixed'."),
        Parameter("bins", "integer", "Histogram: number of equal-width bins (1-100); default "
                  "the Freedman–Diaconis rule."),
        Parameter("dots", "integer", "Dotplot: number of dots (5-100), each 1/dots of the "
                  "sample.", default=20),
        Parameter("title", "string", "Figure title."),
        Parameter("caption", "string", "Figure caption; the template appends n and the method."),
        Parameter("x_label", "string", "Value axis label; default: the column's description, "
                  "else its name."),
        THEME_PARAM,
    )
    columns = (
        ColumnRequirement("sample", "value_column", ("float", "int"),
                          "the sample values; the unit becomes the value axis' unit"),
        ColumnRequirement("group", "group_column", ("str", "int", "bool"), "group of each value",
                          unit="not_applicable", drawn=False),
    )
    notes = (
        "Nothing is fitted: the histogram counts, the KDE and the ECDF are computed from the "
        "sample as documented and their method, bandwidth and n are stated in the caption.",
        "Null values are dropped and counted (warning null_samples_dropped).",
    )

    # ----------------------------------------------------------------- checks
    def _check(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> None:
        assert data is not None
        check_theme(p, issues)
        disp = p["display"]
        if p["group_column"] is not None and disp != "ecdf":
            issues.error(param_path("group_column"), "conflicting_parameters",
                         "groups are compared as ECDFs (display 'ecdf'); overlaid histograms "
                         "or dotplots hide each other.")
        if p["group_column"] == p["value_column"]:
            issues.error(param_path("group_column"), "conflicting_parameters",
                         "group_column and value_column must be different columns.")
        fixed = p["bandwidth_method"] == "fixed"
        if fixed and p["bandwidth"] is None:
            issues.error(param_path("bandwidth"), "missing_parameter",
                         "bandwidth_method 'fixed' needs bandwidth (in the sample's unit).")
        elif not fixed and p["bandwidth"] is not None:
            issues.error(param_path("bandwidth"), "conflicting_parameters",
                         "a bandwidth is given but bandwidth_method is "
                         f"{p['bandwidth_method']!r}; set bandwidth_method 'fixed' or drop it.")
        elif fixed and p["bandwidth"] <= 0:
            issues.error(param_path("bandwidth"), "invalid_parameter",
                         f"bandwidth must be > 0, got {p['bandwidth']!r}.")
        if p["bins"] is not None and not 1 <= p["bins"] <= MAX_BINS:
            issues.error(param_path("bins"), "invalid_parameter",
                         f"bins must be between 1 and {MAX_BINS}, got {p['bins']}.")
        if not 5 <= p["dots"] <= 100:
            issues.error(param_path("dots"), "invalid_parameter",
                         f"dots must be between 5 and 100, got {p['dots']}.")
        if issues.errors:
            return
        vname = p["value_column"]
        values = data.columns[vname].values
        if len(values) > MAX_SAMPLES:
            issues.error(column_path(vname), "invalid_data",
                         f"{len(values)} samples; at most {MAX_SAMPLES} are drawn in bounded "
                         "time. Subsample upstream (and say so in the caption).")
            return
        groups = self._groups(data, p)
        if p["group_column"] is not None:
            if any(g is None for g in data.columns[p["group_column"]].values):
                issues.error(column_path(p["group_column"]), "null_in_key_column",
                             "some values have no group; those rows cannot be placed.")
                return
            if len(groups) > MAX_GROUPS:
                issues.error(column_path(p["group_column"]), "invalid_column_values",
                             f"{len(groups)} groups; at most {MAX_GROUPS} curves stay legible.")
                return
        nulls = sum(v is None for v in values)
        if nulls:
            issues.warn(column_path(vname), "null_samples_dropped",
                        f"{nulls} null value(s) in {vname!r} are not samples; they are left out "
                        "and n counts only the values drawn.")
        need = p["dots"] if disp == "dotplot" else 2
        for g, xs in groups.items():
            where = f" in group {g!r}" if g is not None else ""
            if len(xs) < need:
                issues.error(column_path(vname), "invalid_column_values",
                             f"{len(xs)} non-null value(s){where}; a {disp} needs at least "
                             f"{need}" + (" (one per dot; use fewer dots)" if disp == "dotplot"
                                          else "") + ".")
            elif disp == "histogram" and p["kde"] and \
                    bandwidth(xs, p["bandwidth_method"], p["bandwidth"]) <= 0:
                issues.error(column_path(vname), "invalid_column_values",
                             f"every value{where} is equal, so a {p['bandwidth_method']} "
                             "bandwidth is 0 and no density can be estimated; set kde false "
                             "or bandwidth_method 'fixed'.")
        if data.columns[vname].unit is None:
            issues.warn(column_path(vname), "missing_unit",
                        f"no unit is known for {vname!r}; the value axis will have none.")

    @staticmethod
    def _groups(data: DataSnapshot, p: dict[str, Any]) -> dict[Any, list[float]]:
        values = data.columns[p["value_column"]].values
        keys = (data.columns[p["group_column"]].values if p["group_column"] is not None
                else [None] * len(values))
        out: dict[Any, list[float]] = {}
        for g, v in zip(keys, values):
            bucket = out.setdefault(g, [])
            if v is not None:
                bucket.append(float(v))
        for xs in out.values():
            xs.sort()
        return out

    # ------------------------------------------------------------------ build
    def _build(self, data: DataSnapshot | None, p: dict[str, Any], issues: Issues) -> FigureSpec:
        assert data is not None
        loc = locale_for(p["theme"])
        vname = p["value_column"]
        vcol = data.columns[vname]
        groups = self._groups(data, p)
        spec = FigureSpec(title=p["title"], theme=p["theme"])
        meta: dict[str, Any] = {"input_hash": data.content_hash(), "display": p["display"],
                                "label_locale": loc.tag}
        lines = [p["caption"]] if p["caption"] else []
        x_axis = Axis(label=p["x_label"] or vcol.description or vname, unit=vcol.unit)
        unit_text = f" {vcol.unit}" if vcol.unit else ""
        if p["display"] == "histogram":
            y_axis = self._histogram(spec, data, p, groups[None], meta, lines, loc, unit_text)
        elif p["display"] == "ecdf":
            y_axis = self._ecdf(spec, data, p, groups, meta, lines, loc)
        else:
            y_axis = self._dotplot(spec, data, p, groups[None], meta, lines, loc, unit_text)
        spec.axes = {"x": x_axis, "y": y_axis}
        spec.caption = "\n".join(lines)
        spec.metadata["template"] = meta
        return spec

    def _histogram(self, spec: FigureSpec, data: DataSnapshot, p: dict[str, Any],
                   xs: list[float], meta: dict[str, Any], lines: list[str], loc: Any,
                   unit_text: str) -> Axis:
        vname = p["value_column"]
        rows = [i for i, v in enumerate(data.columns[vname].values) if v is not None]
        spec.datasets["samples"] = copy_rows(data, [vname], rows)
        n, lo, hi = len(xs), xs[0], xs[-1]
        iqr = quantile7(xs, 0.75) - quantile7(xs, 0.25)
        if p["bins"] is not None:
            bins, rule = p["bins"], f"{p['bins']} equal-width bins (given)"
        elif iqr > 0 and hi > lo:
            width = 2 * iqr * n ** (-1 / 3)
            bins = min(MAX_BINS, max(1, math.ceil((hi - lo) / width)))
            rule = "Freedman–Diaconis rule, width 2·IQR·n^(−1/3)" + (
                f", capped at {MAX_BINS} bins" if bins == MAX_BINS else "")
        else:
            bins, rule = math.ceil(math.log2(n)) + 1, "Sturges' rule, ⌈log2 n⌉ + 1"
        if hi == lo:
            lo, hi = lo - 0.5, hi + 0.5
        edges = [store(lo + (hi - lo) * j / bins) for j in range(bins + 1)]
        edges[0], edges[-1] = lo, hi  # every sample inside, exactly
        spec.layers.append(HistogramMark(data="samples", encoding={"x": vname},
                                         bin_edges=edges, density=True,
                                         name=f"Histogram, n = {number_text(n, loc, 12)}"))
        width = number_text(edges[1] - edges[0], loc)
        meta["histogram"] = {"bins": bins, "rule": rule, "bin_edges": edges, "n": n}
        lines.append(f"Histogram: {bins} bins {width}{unit_text} wide ({rule}); n = "
                     f"{number_text(n, loc, 12)}, scaled to density (area 1).")
        if p["kde"]:
            h = bandwidth(xs, p["bandwidth_method"], p["bandwidth"])
            gx, gy = _kde(xs, h)
            hs = number_text(h, loc, 3)
            spec.datasets["kde"] = DataSnapshot(
                columns={vname: Column([store(v) for v in gx], dtype="float",
                                       unit=data.columns[vname].unit),
                         "density": Column([store(v) for v in gy], dtype="float",
                                           unit=self._density_unit(data, p))},
                provenance=spec.datasets["samples"].provenance)
            spec.layers.append(LineMark(data="kde", encoding={"x": vname, "y": "density"},
                                        name=f"Kernel density estimate, h = {hs}"
                                             f"{unit_text.replace(' ', NBSP)}"))
            meta["kde"] = {"kernel": "gaussian", "bandwidth": store(h),
                           "bandwidth_method": p["bandwidth_method"],
                           "bandwidth_rule": _BW_TEXT[p["bandwidth_method"]],
                           "grid": {"points": KDE_POINTS, "reach_bandwidths": KDE_REACH}}
            lines.append(f"Curve: Gaussian kernel density estimate, bandwidth h = {hs}"
                         f"{unit_text} ({_BW_TEXT[p['bandwidth_method']]}).")
        return Axis(label="Density", unit=self._density_unit(data, p), limits=[0.0, None])

    @staticmethod
    def _density_unit(data: DataSnapshot, p: dict[str, Any]) -> str | None:
        unit = data.columns[p["value_column"]].unit
        return f"1/{unit}" if unit else None

    def _ecdf(self, spec: FigureSpec, data: DataSnapshot, p: dict[str, Any],
              groups: dict[Any, list[float]], meta: dict[str, Any], lines: list[str],
              loc: Any) -> Axis:
        vname, gname = p["value_column"], p["group_column"]
        xs_all, ys_all, gs_all = [], [], []
        for g, xs in groups.items():
            n = len(xs)
            for i, x in enumerate(xs):  # riser at every value: (x, i/n) -> (x, (i+1)/n)
                xs_all += [x, x]
                ys_all += [store(100.0 * i / n), store(100.0 * (i + 1) / n)]
                gs_all += [g, g]
        cols = {vname: Column(xs_all, dtype="float", unit=data.columns[vname].unit),
                "share": Column(ys_all, dtype="float", unit="%",
                                description="share of the sample at or below the value")}
        enc = {"x": vname, "y": "share"}
        counts = {g: len(xs) for g, xs in groups.items()}
        if gname is not None:
            gcol = data.columns[gname]
            cols[gname] = Column(gs_all, dtype=gcol.dtype)
            enc["series"] = gname
            lines.append("Empirical CDFs, n = " + "; ".join(
                f"{g}: {number_text(c, loc, 12)}" for g, c in counts.items()) + ".")
        else:
            lines.append(f"Empirical CDF of n = {number_text(counts[None], loc, 12)} values.")
        spec.datasets["ecdf"] = DataSnapshot(columns=cols,
                                             provenance=copy.deepcopy(data.provenance))
        spec.layers.append(LineMark(data="ecdf", encoding=enc,
                                    name="Empirical CDF" if gname is None else None))
        meta["ecdf"] = {"n": {str(g): c for g, c in counts.items()},
                        "steps": "a vertical riser of 1/n at every sample value"}
        return Axis(label="Share of the sample ≤ x", unit="%", limits=[0.0, 100.0])

    def _dotplot(self, spec: FigureSpec, data: DataSnapshot, p: dict[str, Any],
                 xs: list[float], meta: dict[str, Any], lines: list[str], loc: Any,
                 unit_text: str) -> Axis:
        k, n = p["dots"], len(xs)
        qs = [quantile7(xs, (i + 0.5) / k) for i in range(k)]
        columns = max(4, round(1.5 * math.sqrt(k)))
        width = (qs[-1] - qs[0]) / columns if qs[-1] > qs[0] else 1.0
        stacks: dict[int, int] = {}
        dx, dy = [], []
        for q in qs:
            b = min(columns - 1, int((q - qs[0]) / width))
            stacks[b] = stacks.get(b, 0) + 1
            dx.append(store(qs[0] + (b + 0.5) * width))
            dy.append(stacks[b])
        vname = p["value_column"]
        spec.datasets["dots"] = DataSnapshot(
            columns={vname: Column(dx, dtype="float", unit=data.columns[vname].unit),
                     "dot": Column(dy, dtype="int", description="position in its stack")},
            provenance=copy.deepcopy(data.provenance))
        share = 100 / k
        share_text = f"{number_text(share, loc, 3)}{loc.percent_sep}%"
        spec.layers.append(ScatterMark(data="dots", encoding={"x": vname, "y": "dot"},
                                       name=f"{k} dots, each {share_text} of the sample",
                                       style={"marker": "o"}))
        meta["dotplot"] = {"dots": k, "n": n, "quantiles": [store(q) for q in qs],
                           "probabilities": "(i - 0.5) / dots, i = 1..dots",
                           "column_width": store(width), "columns": columns}
        lines.append(f"Quantile dotplot: each dot is {share_text} of n = "
                     f"{number_text(n, loc, 12)} values, at the sample quantiles "
                     f"(i − {number_text(0.5, loc)})/{k}, stacked in columns "
                     f"{number_text(width, loc, 3)}{unit_text} wide.")
        return Axis(label=f"Dots, stacked (each {share_text})",
                    limits=[0.0, float(max(dy) + 1)])

    def example(self) -> dict[str, Any]:
        import random

        rng = random.Random(11)
        values = [round(rng.lognormvariate(5.3, 0.35), 1) for _ in range(400)]
        snap = DataSnapshot.from_columns({"latency": values}, units={"latency": "ms"})
        snap.columns["latency"].description = "Response time"
        snap.provenance.source = "example"
        snap.provenance.notes = ("Synthetic: 400 draws from a log-normal distribution "
                                 "(Python random.Random, seed 11); not a measurement.")
        snap.provenance.extra = {"synthetic": True, "seed": 11}
        from ..spec.data import dataset_to_dict

        return {"data": dataset_to_dict(snap), "params": {
            "value_column": "latency", "display": "histogram",
            "title": "Response time of 400 requests",
            "caption": "Synthetic sample (seeded), not a measurement."}}


def _kde(xs: list[float], h: float) -> tuple[list[float], list[float]]:
    """Gaussian KDE of ``xs`` with bandwidth ``h`` on :data:`KDE_POINTS`
    points from ``KDE_REACH`` bandwidths below the smallest to above the
    largest value. numpy (imported here only) bounds the time: chunks of
    the grid against every sample."""
    import numpy as np

    x = np.asarray(xs, dtype=float)
    grid = np.linspace(x[0] - KDE_REACH * h, x[-1] + KDE_REACH * h, KDE_POINTS)
    out = np.empty(KDE_POINTS)
    step = max(1, 4_000_000 // x.size)
    norm = 1.0 / (x.size * h * math.sqrt(2 * math.pi))
    for a in range(0, KDE_POINTS, step):
        z = (grid[a:a + step, None] - x[None, :]) / h
        out[a:a + step] = np.exp(-0.5 * z * z).sum(axis=1) * norm
    return grid.tolist(), out.tolist()
