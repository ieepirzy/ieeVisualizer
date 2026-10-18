"""Small seeded Monte Carlo ensembles for figures (fan charts, ensembles).

Plot-focused, not a simulation library (heavy simulation belongs upstream):
two classic processes, a fixed generator, hard size caps, and every fact
needed to reproduce the paths recorded in the snapshot's provenance.

* :func:`random_walk`: Gaussian random walk (discretised Brownian motion
  with drift), ``x[t+dt] = x[t] + drift*dt + sigma*sqrt(dt)*Z``.
* :func:`gbm`: geometric Brownian motion, simulated exactly on the grid,
  ``s[t+dt] = s[t] * exp((mu - sigma**2/2)*dt + sigma*sqrt(dt)*Z)``.

``Z`` are standard normal draws from :class:`random.Random` seeded with
``seed`` (``normalvariate``), drawn path by path, step by step. Python's
generator, not numpy's, so the same seed gives the same paths on every
Python >= 3.9 and numpy version; values are stored to 10 significant digits
(the last bits of ``math.exp`` may differ between machines).

The result is a long-format :class:`~ieeVisualizer.spec.DataSnapshot`
(``time``, ``path``, ``value``) ready for the ``fan_chart`` template, whose
provenance says it is synthetic: ``source`` names the function, ``notes``
says "Synthetic ... not a measurement" with the seed, and ``extra`` holds
``{"synthetic": true, "seed", "model", "parameters", "generator"}``.
"""

from __future__ import annotations

import math
import random
from typing import Any

from .spec import DataSnapshot, Provenance
from .spec.data import Column

#: Most paths, steps, and values (paths x (steps + 1)) one call may simulate.
MAX_PATHS = 2000
MAX_STEPS = 2000
MAX_VALUES = 200_000
GENERATOR = "Python random.Random (Mersenne Twister), normalvariate, path by path"


def _store(v: float) -> float:
    out = float(f"{v:.10g}")
    return 0.0 if out == 0 else out


def _check(paths: Any, steps: Any, seed: Any, dt: Any, sigma: Any) -> None:
    for name, v, top in (("paths", paths, MAX_PATHS), ("steps", steps, MAX_STEPS)):
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= top:
            raise ValueError(f"{name} must be an integer from 1 to {top}, got {v!r}.")
    if paths * (steps + 1) > MAX_VALUES:
        raise ValueError(f"paths x (steps + 1) = {paths * (steps + 1)} values; at most "
                         f"{MAX_VALUES} are simulated here. Simulate upstream for more.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"seed must be an integer (it is recorded so the paths can be "
                         f"reproduced), got {seed!r}.")
    for name, v in (("dt", dt), ("sigma", sigma)):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) \
                or v <= 0:
            raise ValueError(f"{name} must be a finite number > 0, got {v!r}.")


def _snapshot(model: str, func: str, params: dict[str, Any], paths: int, steps: int,
              seed: int, dt: float, values: list[list[float]], unit: str | None,
              time_unit: str | None) -> DataSnapshot:
    times, ids, vals = [], [], []
    for p, path in enumerate(values):
        for s, v in enumerate(path):
            times.append(_store(s * dt))
            ids.append(p)
            vals.append(_store(v))
    snap = DataSnapshot(
        columns={"time": Column(times, dtype="float", unit=time_unit),
                 "path": Column(ids, dtype="int", description="simulated path"),
                 "value": Column(vals, dtype="float", unit=unit,
                                 description="simulated value")},
        provenance=Provenance(
            source=f"ieeVisualizer.stochastic.{func}",
            notes=f"Synthetic: {paths} seeded Monte Carlo paths of a {model} (seed {seed}); "
                  "not a measurement.",
            extra={"synthetic": True, "seed": seed, "model": model,
                   "parameters": dict(params, paths=paths, steps=steps, dt=dt),
                   "generator": GENERATOR}))
    return snap


def random_walk(paths: int, steps: int, seed: int, *, start: float = 0.0, drift: float = 0.0,
                sigma: float = 1.0, dt: float = 1.0, unit: str | None = None,
                time_unit: str | None = None) -> DataSnapshot:
    """``paths`` Gaussian random walks of ``steps`` steps from ``start``."""
    _check(paths, steps, seed, dt, sigma)
    for name, v in (("start", start), ("drift", drift)):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"{name} must be a finite number, got {v!r}.")
    rng = random.Random(seed)
    sd = sigma * math.sqrt(dt)
    out = []
    for _ in range(paths):
        x, path = float(start), [float(start)]
        for _ in range(steps):
            x += drift * dt + sd * rng.normalvariate(0.0, 1.0)
            path.append(x)
        out.append(path)
    return _snapshot("Gaussian random walk", "random_walk",
                     {"start": start, "drift": drift, "sigma": sigma}, paths, steps, seed,
                     float(dt), out, unit, time_unit)


def gbm(paths: int, steps: int, seed: int, *, s0: float = 1.0, mu: float = 0.0,
        sigma: float = 0.2, dt: float = 1.0, unit: str | None = None,
        time_unit: str | None = None) -> DataSnapshot:
    """``paths`` geometric Brownian motion paths of ``steps`` steps from ``s0``
    (drift ``mu`` and volatility ``sigma`` per unit of time)."""
    _check(paths, steps, seed, dt, sigma)
    for name, v in (("s0", s0), ("mu", mu)):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"{name} must be a finite number, got {v!r}.")
    if s0 <= 0:
        raise ValueError(f"s0 must be > 0 (a GBM stays positive), got {s0!r}.")
    rng = random.Random(seed)
    a, b = (mu - 0.5 * sigma * sigma) * dt, sigma * math.sqrt(dt)
    out = []
    for _ in range(paths):
        log_s, path = math.log(s0), [float(s0)]
        for _ in range(steps):
            log_s += a + b * rng.normalvariate(0.0, 1.0)
            if log_s > 700:  # exp would overflow a float
                raise ValueError("a path overflowed; lower mu, sigma or the horizon.")
            path.append(math.exp(log_s))
        out.append(path)
    return _snapshot("geometric Brownian motion", "gbm", {"s0": s0, "mu": mu, "sigma": sigma},
                     paths, steps, seed, float(dt), out, unit, time_unit)
