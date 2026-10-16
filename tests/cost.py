"""Measure how much work a code path does, independently of machine load.

Several tests guard that a hostile input is refused, or the largest allowed
input is drawn, without a pathological blow-up. They used to compare
wall-clock seconds against a constant, which fails whenever the machine is
busy (two suites side by side took a 30 s PNG budget to 33 s while it takes
under 9 s alone).

``measure`` reports the CPU time of the block instead of wall time (the
scheduler running someone else does not advance it), scaled by a reference
workload timed in the same process just before and just after, so contention
that slows the CPU itself (shared cores, frequency, cache) scales the
reference too. The result is in *reference seconds*: CPU seconds the block
would take on the machine where ``REFERENCE_SECONDS`` was recorded, so the
budgets the tests always had keep their meaning and their size.

A regression to quadratic, exponential or never-returning work still shows
up as a large number of reference seconds; a slow or loaded runner does not.
Used in-process (``import cost``: ``tests/`` is on ``sys.path`` through its
conftest) and by the tests' child processes (``tests`` on their path).
"""

from __future__ import annotations

import time
from contextlib import contextmanager

#: CPU seconds one ``_reference()`` call takes on an idle 2.1 GHz Xeon, the
#: machine the budgets in the tests were checked against.
REFERENCE_SECONDS = 0.1


_NUMBERS = [(i * 2654435761) % 1_000_003 for i in range(200_000)]


def _reference() -> int:
    """A fixed CPU-bound workload: sorting integers. Sorting is C code whose
    speed is the same on every supported Python (a bytecode loop runs 1.5x
    faster on 3.13 than on 3.9, which would make the budgets interpreter
    dependent)."""
    n = 0
    for _ in range(7):
        n += sorted(_NUMBERS)[-1]
    return n


def reference_cpu() -> float:
    """Least CPU time of three reference runs (seconds)."""
    best = float("inf")
    for _ in range(3):
        t = time.process_time()
        _reference()
        best = min(best, time.process_time() - t)
    return best


def reference_seconds(cpu: float, reference: float) -> float:
    """``cpu`` seconds of work, measured next to a reference run that took
    ``reference`` CPU seconds, in reference seconds."""
    return cpu * REFERENCE_SECONDS / reference


class Cost:
    cpu: float = 0.0  #: CPU seconds of the block
    reference: float = 0.0  #: CPU seconds of one reference run around it

    @property
    def seconds(self) -> float:
        """The block's cost in reference seconds."""
        return reference_seconds(self.cpu, self.reference)

    def __repr__(self) -> str:
        return (f"Cost({self.seconds:.3f} reference s; cpu {self.cpu:.3f} s, "
                f"reference run {self.reference:.3f} s)")


@contextmanager
def measure():
    """``with measure() as c: work()`` then ``c.seconds``.

    The reference is timed before and after the block and the larger is used:
    contention that starts or stops during the block still lands in one of
    the two."""
    c = Cost()
    before = reference_cpu()
    t = time.process_time()
    try:
        yield c
    finally:
        c.cpu = time.process_time() - t
        c.reference = max(before, reference_cpu())
