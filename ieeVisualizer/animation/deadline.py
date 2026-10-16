"""The enforced wall-clock budget of one render or export.

The cost estimate is only an early refusal; this is the guarantee.
``render_animation``/``export_animation`` create a :class:`Deadline` (default
:data:`DEFAULT_DEADLINE_S`; ``deadline=`` seconds or a shared Deadline) and
check it between pipeline phases and before every state and frame. By
default the work runs in a worker process killed at the deadline
(:mod:`ieeVisualizer.animation.isolate`), so the bound is wall-clock; inline
the checks are cooperative (best effort). On expiry
:class:`DeadlineExceeded` (``render_deadline_exceeded`` at the root path
``""``, shown as ``/``) carries ``stats``.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from ..render.base import issue
from .base import AnimationError

#: Default budget of one render or export, in seconds.
DEFAULT_DEADLINE_S = 60.0


class Deadline:
    """A wall-clock budget, started when created. ``progress`` is filled in by
    the pipeline (states built, drawn, frames encoded) and reported on expiry."""

    def __init__(self, seconds: float = DEFAULT_DEADLINE_S, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"deadline must be a positive number of seconds, got {seconds!r}.")
        self.seconds = float(seconds)
        self._clock = clock
        self.started = clock()
        self.phase = "start"
        self.progress: dict[str, int] = {}
        #: Per-call memo (validation results by ``animation_hash``), so nested
        #: entry points (export -> render -> adapter) validate once.
        self.memo: dict[Any, Any] = {}
        #: Called with this Deadline at most every 0.1 s from :meth:`check`
        #: (a worker process reports its phase and progress this way).
        self.report: Callable[[Deadline], None] | None = None
        self._reported = self.started

    @classmethod
    def of(cls, value: Deadline | float | None) -> Deadline:
        """``value`` itself if it is a Deadline, else a new one of ``value``
        seconds (``None``: :data:`DEFAULT_DEADLINE_S`)."""
        if isinstance(value, Deadline):
            return value
        return cls(DEFAULT_DEADLINE_S if value is None else value)

    def elapsed(self) -> float:
        return self._clock() - self.started

    def remaining(self) -> float:
        return max(0.0, self.seconds - self.elapsed())

    def count(self, key: str, n: int = 1) -> None:
        self.progress[key] = self.progress.get(key, 0) + n

    def check(self, phase: str | None = None) -> None:
        """Raise :class:`DeadlineExceeded` if the budget is spent; record
        ``phase`` as the current pipeline phase."""
        if phase is not None:
            self.phase = phase
        now = self._clock()
        if self.report is not None and now - self._reported >= 0.1:
            self._reported = now
            self.report(self)
        if now - self.started >= self.seconds:
            raise self.exceeded()

    def exceeded(self) -> DeadlineExceeded:
        stats = {"phase": self.phase, "elapsed_s": round(self.elapsed(), 3),
                 "deadline_s": self.seconds, **self.progress}
        done = ", ".join(f"{k} {v}" for k, v in self.progress.items()) or "nothing finished"
        return DeadlineExceeded(stats, f"the render did not finish within its {self.seconds:g} s "
                                f"deadline and was stopped during {self.phase} after "
                                f"{stats['elapsed_s']:g} s ({done}). Use fewer states, layers, "
                                "rows or samples, simpler expressions, a smaller frame, or a "
                                "longer deadline.")


class DeadlineExceeded(AnimationError):
    """``render_deadline_exceeded``; ``stats`` = phase, elapsed and progress."""

    def __init__(self, stats: dict[str, Any], message: str) -> None:
        self.stats = stats
        super().__init__([issue("", "render_deadline_exceeded", message)])
