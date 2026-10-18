"""Named probability distributions for the ``distribution`` template: their
parameters, closed-form PDF/PMF and CDF, and quantiles.

Pure Python (``math``), so importing the templates package stays free of
numpy and sympy and every value is reproducible across numpy versions. The
PDF/PMF of each family is its textbook closed form, recorded as a SymPy
expression string (``formula``) in the spec metadata; the tests check each
against ``sympy.stats``. CDFs use the standard special functions: ``erfc``
for the normal and log-normal, the regularized incomplete beta function
(beta, Student t, binomial) and the regularized incomplete gamma function
(gamma, Poisson), evaluated by the classical continued fractions (Lentz) and
series with a hard iteration cap, so an evaluation is bounded in time; a
parameter set too extreme to converge within the cap raises
:class:`NumericalLimit` instead of returning an inaccurate number. Quantiles
are found by bisection on the CDF (continuous) or on the integers (discrete:
the smallest k with F(k) >= p).

SciPy is not a dependency and mpmath (shipped with SymPy) fails to converge
on large shape parameters (e.g. ``betainc(5000, 7000, 0, 0.41)``), which is
why the incomplete beta/gamma functions are implemented here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

#: Iteration cap for the continued fractions and series (bounded time).
MAX_ITER = 20000
_EPS = 1e-15
_TINY = 1e-300
#: Largest shape/count parameter accepted (alpha, beta, shape, lambda, n,
#: df): the continued fractions converge in about sqrt(parameter) steps.
MAX_SHAPE = 1e6


#: A continuous quantile x is accepted only if |F(x) - p| is at most this
#: share of the smaller tail, min(p, 1 - p); otherwise it is refused.
QUANTILE_TOLERANCE = 1e-4
_SMALLEST = 5e-324


def _t_tail(nu: float, z: float) -> float:
    """P(|T| > z) for Student t with ``nu`` degrees of freedom: I_x(nu/2, 1/2)
    with x = nu / (nu + z^2), in log form so a huge z (z^2 overflowing, or x
    underflowing) keeps the heavy tail, I_x ~ x^a / (a B(a, 1/2))."""
    a = nu / 2
    log_x = math.log(nu) - 2 * math.log(z) - math.log1p(math.exp(math.log(nu) - 2 * math.log(z)))
    if log_x > -700:
        return betainc(a, 0.5, math.exp(log_x))
    lbeta = math.lgamma(a) + math.lgamma(0.5) - math.lgamma(a + 0.5)
    return math.exp(a * log_x - math.log(a) - lbeta)


class NumericalLimit(ValueError):
    """A special function did not converge within :data:`MAX_ITER` steps."""


# --------------------------------------------------------------------------
# special functions
# --------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            return h
    raise NumericalLimit(f"incomplete beta({a}, {b}, {x}) did not converge")


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def gammainc(a: float, x: float) -> float:
    """Regularized lower incomplete gamma function P(a, x)."""
    if x <= 0.0:
        return 0.0
    log_front = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:  # series
        term = total = 1.0 / a
        ap = a
        for _ in range(MAX_ITER):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                return total * math.exp(log_front)
        raise NumericalLimit(f"incomplete gamma({a}, {x}) did not converge")
    b = x + 1.0 - a  # continued fraction for Q
    c, d = 1.0 / _TINY, 1.0 / b
    h = d
    for i in range(1, MAX_ITER + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = b + an / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            return 1.0 - math.exp(log_front) * h
    raise NumericalLimit(f"incomplete gamma({a}, {x}) did not converge")


# --------------------------------------------------------------------------
# families
# --------------------------------------------------------------------------

#: How a parameter is constrained: (check, words for the message).
_RULES: dict[str, tuple[Callable[[float], bool], str]] = {
    "real": (lambda v: True, "a finite number"),
    "positive": (lambda v: 0 < v <= MAX_SHAPE, f"a number > 0 (at most {MAX_SHAPE:g})"),
    "scale": (lambda v: v > 0, "a number > 0"),
    "probability": (lambda v: 0 <= v <= 1, "a probability in [0, 1]"),
    "count": (lambda v: float(v).is_integer() and 1 <= v <= MAX_SHAPE,
              f"a whole number >= 1 (at most {MAX_SHAPE:g})"),
}


@dataclass(frozen=True)
class Param:
    name: str
    rule: str
    symbol: str
    default: float | None = None


@dataclass(frozen=True)
class Family:
    name: str
    title: str
    discrete: bool
    params: tuple[Param, ...]
    #: closed-form PDF (continuous) or PMF (discrete) as a SymPy expression
    #: of ``x`` (``k``) and the parameter names
    formula: str
    #: lower end of the support when it is bounded (0), else None
    lower: float | None
    upper: float | None = None

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)


_N = "exp(-(x - mu)**2/(2*sigma**2))/(sigma*sqrt(2*pi))"
FAMILIES: dict[str, Family] = {f.name: f for f in (
    Family("normal", "Normal", False,
           (Param("mu", "real", "μ"), Param("sigma", "scale", "σ")), _N, None),
    Family("lognormal", "Log-normal", False,
           (Param("mu", "real", "μ"), Param("sigma", "scale", "σ")),
           "exp(-(log(x) - mu)**2/(2*sigma**2))/(x*sigma*sqrt(2*pi))", 0.0),
    Family("beta", "Beta", False,
           (Param("alpha", "positive", "α"), Param("beta", "positive", "β")),
           "x**(alpha - 1)*(1 - x)**(beta - 1)*gamma(alpha + beta)/(gamma(alpha)*gamma(beta))",
           0.0, 1.0),
    Family("gamma", "Gamma", False,
           (Param("shape", "positive", "k"), Param("scale", "scale", "θ")),
           "x**(shape - 1)*exp(-x/scale)/(gamma(shape)*scale**shape)", 0.0),
    Family("student_t", "Student t", False,
           (Param("df", "positive", "ν"), Param("loc", "real", "μ", 0.0),
            Param("scale", "scale", "σ", 1.0)),
           "gamma((df + 1)/2)/(sqrt(df*pi)*gamma(df/2)*scale)"
           "*(1 + ((x - loc)/scale)**2/df)**(-(df + 1)/2)", None),
    Family("poisson", "Poisson", True, (Param("lambda", "positive", "λ"),),
           "lamda**k*exp(-lamda)/factorial(k)", 0.0),  # SymPy spells it lamda (a keyword)
    Family("binomial", "Binomial", True,
           (Param("n", "count", "n"), Param("p", "probability", "p")),
           "binomial(n, k)*p**k*(1 - p)**(n - k)", 0.0),
)}


def param_problem(family: Family, name: str, value: Any) -> str | None:
    """Why ``value`` is not a valid ``name`` of ``family`` (None if it is)."""
    check, words = _RULES[family.param(name).rule]
    ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
          and math.isfinite(value) and check(float(value)))
    if ok:
        return None
    return (f"{family.title} parameter {name!r} ({family.param(name).symbol}) must be {words}, "
            f"got {value!r}.")


class Dist:
    """One distribution with resolved parameters."""

    def __init__(self, family: Family, params: dict[str, float]) -> None:
        self.family = family
        self.p = {k: float(v) for k, v in params.items()}

    @property
    def discrete(self) -> bool:
        return self.family.discrete

    # ---------------------------------------------------------------- density
    def density(self, x: float) -> float:
        """PDF (continuous; ``inf`` where unbounded) or PMF (discrete, integer x)."""
        p, name = self.p, self.family.name
        if name == "normal":
            z = (x - p["mu"]) / p["sigma"]
            return math.exp(-0.5 * z * z) / (p["sigma"] * math.sqrt(2 * math.pi))
        if name == "lognormal":
            if x <= 0:
                return 0.0
            z = (math.log(x) - p["mu"]) / p["sigma"]
            return math.exp(-0.5 * z * z) / (x * p["sigma"] * math.sqrt(2 * math.pi))
        if name == "beta":
            a, b = p["alpha"], p["beta"]
            if x < 0 or x > 1:
                return 0.0
            if x in (0.0, 1.0):
                e = a if x == 0.0 else b
                if e < 1:
                    return math.inf
                if e > 1:
                    return 0.0
                return math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b))
            return math.exp((a - 1) * math.log(x) + (b - 1) * math.log1p(-x)
                            + math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b))
        if name == "gamma":
            k, th = p["shape"], p["scale"]
            if x < 0:
                return 0.0
            if x == 0:
                return math.inf if k < 1 else (1.0 / th if k == 1 else 0.0)
            return math.exp((k - 1) * math.log(x) - x / th - math.lgamma(k) - k * math.log(th))
        if name == "student_t":
            nu, s = p["df"], p["scale"]
            z = (x - p["loc"]) / s
            return math.exp(math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2)
                            - 0.5 * math.log(nu * math.pi) - math.log(s)
                            - (nu + 1) / 2 * math.log1p(z * z / nu))
        k = int(x)
        if name == "poisson":
            lam = p["lambda"]
            return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1)) if k >= 0 else 0.0
        n, q = int(p["n"]), p["p"]
        if k < 0 or k > n:
            return 0.0
        if q in (0.0, 1.0):
            return 1.0 if k == (0 if q == 0.0 else n) else 0.0
        return math.exp(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                        + k * math.log(q) + (n - k) * math.log1p(-q))

    def unbounded_at(self) -> list[float]:
        """Ends of the support where the PDF is infinite."""
        p, name = self.p, self.family.name
        if name == "beta":
            return [x for x, e in ((0.0, p["alpha"]), (1.0, p["beta"])) if e < 1]
        if name == "gamma" and p["shape"] < 1:
            return [0.0]
        return []

    # -------------------------------------------------------------------- CDF
    def cdf(self, x: float) -> float:
        p, name = self.p, self.family.name
        if name == "normal":
            return 0.5 * math.erfc(-(x - p["mu"]) / (p["sigma"] * math.sqrt(2)))
        if name == "lognormal":
            if x <= 0:
                return 0.0
            return 0.5 * math.erfc(-(math.log(x) - p["mu"]) / (p["sigma"] * math.sqrt(2)))
        if name == "beta":
            return betainc(p["alpha"], p["beta"], x)
        if name == "gamma":
            return gammainc(p["shape"], x / p["scale"]) if x > 0 else 0.0
        if name == "student_t":
            nu = p["df"]
            z = (x - p["loc"]) / p["scale"]
            if z * z < nu:  # near the centre: nu / (nu + z^2) would round to 1
                half = 0.5 * betainc(0.5, nu / 2, z * z / (nu + z * z))
                return 0.5 + half if z > 0 else 0.5 - half
            tail = 0.5 * _t_tail(nu, abs(z))
            return 1.0 - tail if z > 0 else tail
        k = math.floor(x)
        if k < 0:
            return 0.0
        if name == "poisson":  # P(X <= k) = Q(k + 1, lambda)
            return 1.0 - gammainc(k + 1.0, p["lambda"])
        n, q = int(p["n"]), p["p"]
        if k >= n:
            return 1.0
        if q in (0.0, 1.0):
            return 1.0 if q == 0.0 else 0.0
        return betainc(n - k, k + 1.0, 1.0 - q)

    # --------------------------------------------------------------- quantile
    def quantile(self, prob: float) -> float:
        """The p-quantile: the x with F(x) = p (continuous), or the smallest
        integer k with F(k) >= p (discrete)."""
        if self.discrete:
            lo, hi = -1, self._upper_int()
            while hi - lo > 1:  # invariant: F(lo) < p <= F(hi)
                mid = (lo + hi) // 2
                if self.cdf(mid) >= prob * (1 - 1e-12):
                    hi = mid
                else:
                    lo = mid
            return float(hi)
        name, p = self.family.name, self.p
        if name == "lognormal":
            normal = Dist(FAMILIES["normal"], {"mu": p["mu"], "sigma": p["sigma"]})
            return math.exp(normal.quantile(prob))
        if self.family.lower == 0.0:
            x = self._log_bisect(prob)
        else:
            lo, hi = self._bracket(prob)
            for _ in range(400):
                mid = 0.5 * (lo + hi)
                if mid in (lo, hi):
                    break
                if self.cdf(mid) < prob:
                    lo = mid
                else:
                    hi = mid
            x = 0.5 * (lo + hi)
        if abs(self.cdf(x) - prob) > QUANTILE_TOLERANCE * min(prob, 1 - prob):
            raise NumericalLimit(f"the {prob:.6g} quantile cannot be represented accurately in "
                                 f"floating point (F({x!r}) = {self.cdf(x)!r})")
        return x

    def _log_bisect(self, prob: float) -> float:
        """Bisection on log x for a support bounded at 0, so a quantile far
        below 1 (P5 of Gamma(0.01) is about 1e-130) is found, not rounded to
        the bracket's end; one below the smallest float is refused."""
        hi = self.family.upper if self.family.upper is not None else 1.0
        while self.cdf(hi) < prob:
            hi *= 2.0
            if math.isinf(hi):
                raise NumericalLimit(f"no bracket for the {prob} quantile")
        lo_t, hi_t = math.log(_SMALLEST), math.log(hi)
        if self.cdf(_SMALLEST) >= prob:
            raise NumericalLimit(f"the {prob:.6g} quantile is below the smallest float "
                                 f"({_SMALLEST!r})")
        for _ in range(200):
            mid = 0.5 * (lo_t + hi_t)
            if mid in (lo_t, hi_t):
                break
            if self.cdf(math.exp(mid)) < prob:
                lo_t = mid
            else:
                hi_t = mid
        return math.exp(0.5 * (lo_t + hi_t))

    def _upper_int(self) -> int:
        if self.family.name == "binomial":
            return int(self.p["n"])
        lam = self.p["lambda"]
        return int(lam + 40 * math.sqrt(lam) + 60)

    def _bracket(self, prob: float) -> tuple[float, float]:
        f = self.family
        if f.lower is not None and f.upper is not None:
            return f.lower, f.upper
        centre, step = self._centre_scale()
        lo = f.lower if f.lower is not None else centre - step
        hi = centre + step
        for _ in range(2000):
            if f.lower is None and self.cdf(lo) > prob:
                lo = centre - (centre - lo) * 2
            elif self.cdf(hi) < prob:
                hi = centre + (hi - centre) * 2
            else:
                return lo, hi
        raise NumericalLimit(f"no bracket for the {prob} quantile")

    def _centre_scale(self) -> tuple[float, float]:
        p, name = self.p, self.family.name
        if name == "normal":
            return p["mu"], p["sigma"]
        if name == "student_t":
            return p["loc"], p["scale"]
        mean = p["shape"] * p["scale"]  # gamma
        return mean, max(mean, p["scale"])


def resolved_params(family: Family, given: dict[str, Any]) -> dict[str, float]:
    out = {}
    for prm in family.params:
        v = given.get(prm.name, prm.default)
        out[prm.name] = float(v)
    return out
