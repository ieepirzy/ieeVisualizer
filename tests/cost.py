"""Measure how much work a code path does, independently of machine load.

Several tests guard that a hostile input is refused, or the largest allowed
input is drawn, without a pathological blow-up. They used to compare
wall-clock seconds against a constant, which fails whenever the machine is
busy (two suites side by side took a 30 s PNG budget to 33 s while it takes
under 9 s alone).

``measure`` reports the CPU time of the block instead of wall time (the
scheduler running someone else does not advance it), scaled by a reference
workload timed in the same process just before the block, so contention for
a CPU share (shared cores, frequency) scales the reference too. The result is
in *reference seconds*: CPU seconds the block would take on the machine where
``REFERENCE_SECONDS`` was recorded, so the budgets the tests always had keep
their meaning and their size. ``within`` also holds the block's wall time to
``WALL_FACTOR`` times the budget, so a regression that waits instead of
computing (a sleep, a lock, a blocking read) is still caught.

What it measures, and what it does not:

* CPU of this process (every thread) and of child processes that were
  started *and reaped* (waited for) inside the block, via ``os.times()``. A
  child still running, or not yet waited for, when the block ends is not
  counted (on Windows ``os.times()`` reports no child time at all).
* The reference is a CPU-bound sort of a small list that stays in cache: it
  tracks contention for CPU share, not for memory bandwidth. A block bound by
  memory traffic can still be slowed by a neighbour hammering memory more
  than the reference is; the budgets leave room for that, the wall backstop
  is ten times the budget.
* Work that never returns never reaches the measurement at all: those
  regressions are caught by the subprocess timeouts the tests keep as hang
  guards. Quadratic or exponential work that does return shows up as a large
  number of reference seconds; a slow or loaded runner does not.
* Nested ``measure`` blocks: the reference runs of the inner one are
  subtracted from the outer one (CPU and wall); the inner block's own work
  counts in both.

Used in-process (``import cost``: pytest's default ``prepend`` import mode
puts ``tests/`` on ``sys.path`` because ``tests/conftest.py`` has no
``__init__.py`` beside it) and by the tests' child processes, which get
``tests`` on their path explicitly.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

#: CPU seconds one ``_reference()`` call takes on an idle 2.1 GHz Xeon, the
#: machine the budgets in the tests were checked against.
REFERENCE_SECONDS = 0.1

#: A block may take at most this many times its budget in wall seconds.
WALL_FACTOR = 10.0

_NUMBERS = [(i * 2654435761) % 1_000_003 for i in range(200_000)]

#: CPU and wall seconds spent in reference runs so far (subtracted from any
#: block they ran inside).
_overhead = [0.0, 0.0]


def _reference() -> int:
    """A fixed CPU-bound workload: sorting integers. Sorting is C code whose
    speed is the same on every supported Python (a bytecode loop runs 1.5x
    faster on 3.13 than on 3.9, which would make the budgets interpreter
    dependent)."""
    n = 0
    for _ in range(7):
        n += sorted(_NUMBERS)[-1]
    return n


def _cpu() -> float:
    """CPU seconds of this process plus its reaped children."""
    t = os.times()
    return time.process_time() + t.children_user + t.children_system


def reference_cpu() -> float:
    """Least CPU time of three reference runs (seconds)."""
    wall, cpu = time.perf_counter(), time.process_time()
    best = float("inf")
    for _ in range(3):
        t = time.process_time()
        _reference()
        best = min(best, time.process_time() - t)
    _overhead[0] += time.process_time() - cpu
    _overhead[1] += time.perf_counter() - wall
    return best


def reference_seconds(cpu: float, reference: float) -> float:
    """``cpu`` seconds of work, measured next to a reference run that took
    ``reference`` CPU seconds, in reference seconds."""
    return cpu * REFERENCE_SECONDS / reference


def within(budget: float, seconds: float, wall: float) -> bool:
    """``seconds`` (reference seconds) under ``budget``, and ``wall`` under
    ``WALL_FACTOR * budget``."""
    return seconds < budget and wall < WALL_FACTOR * budget


class Cost:
    def __init__(self) -> None:
        self.reference = 0.0  #: CPU seconds of one reference run before the block
        self._cpu: float | None = None
        self._wall: float | None = None

    def _done(self) -> tuple[float, float]:
        if self._cpu is None or self._wall is None:
            raise RuntimeError("a Cost is known only after its `with measure()` block ends")
        return self._cpu, self._wall

    @property
    def cpu(self) -> float:
        """CPU seconds of the block (this process and its reaped children)."""
        return self._done()[0]

    @property
    def wall(self) -> float:
        """Wall seconds of the block."""
        return self._done()[1]

    @property
    def seconds(self) -> float:
        """The block's cost in reference seconds."""
        return reference_seconds(self.cpu, self.reference)

    def within(self, budget: float) -> bool:
        return within(budget, self.seconds, self.wall)

    def __repr__(self) -> str:
        if self._cpu is None:
            return "Cost(running)"
        return (f"Cost({self.seconds:.3f} reference s; cpu {self.cpu:.3f} s, "
                f"wall {self.wall:.3f} s, reference run {self.reference:.3f} s)")


@contextmanager
def measure():
    """``with measure() as c: work()`` then ``c.seconds`` / ``c.within(budget)``.

    The reference is timed once, just before the block. (Timing it after as
    well and taking the larger would let work the block leaves running in the
    background slow the reference down and so excuse itself.)"""
    c = Cost()
    c.reference = reference_cpu()
    cpu0, wall0 = _overhead[0], _overhead[1]
    t, w = _cpu(), time.perf_counter()
    try:
        yield c
    finally:
        c._cpu = _cpu() - t - (_overhead[0] - cpu0)
        c._wall = time.perf_counter() - w - (_overhead[1] - wall0)
