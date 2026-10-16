"""Frame-count arithmetic and the frame/duration/poster guards, from the
spec's numbers alone (an oversized spec is refused in microseconds)."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from ..spec import ValidationIssue
from .spec import MAX_DURATION_S, MAX_FRAMES, AnimationSpec, FrameColumn, ParameterSweep


def sweep_state_count(tl: ParameterSweep, fps: int) -> int:
    """``len(values)``, or ``duration * fps`` rounded half up in decimal on
    the number as written (4.1 s at 15 fps: 62), at least 2."""
    if tl.values is not None:
        return len(tl.values)
    x = float(tl.duration) * fps
    if not math.isfinite(x):
        raise ValueError(f"duration * fps = {x!r} is not finite.")
    exact = Decimal(repr(float(tl.duration))) * Decimal(int(fps))
    return max(2, int(exact.to_integral_value(rounding=ROUND_HALF_UP)))


def repeat_of(anim: AnimationSpec) -> int:
    """Output frames per state (``frames_per_value`` of a frame column, else 1)."""
    tl = anim.timeline
    return tl.frames_per_value if isinstance(tl, FrameColumn) else 1


def frame_total(anim: AnimationSpec, n_states: int) -> int:
    """Output frames for ``n_states`` states: holds plus each state repeated."""
    return anim.hold_start + anim.hold_end + n_states * repeat_of(anim)


def frame_limit_issues(anim: AnimationSpec, n_states: int, *, resolved: bool = False,
                       lower_bound: bool = False) -> list[ValidationIssue]:
    """``too_many_frames``, ``duration_too_long`` and ``invalid_poster`` for an
    animation with ``n_states`` states (all arithmetic; nothing is built).
    ``lower_bound``: ``n_states`` is a minimum (data not resolved yet)."""
    n = frame_total(anim, n_states)
    src = ("the resolved data gives" if resolved
           else "the animation has at least" if lower_bound else "the animation has")
    out = []
    if n > MAX_FRAMES:
        out.append(ValidationIssue(
            "/timeline", "too_many_frames",
            f"{src} {n} frames; at most {MAX_FRAMES} are rendered. Shorten the duration, "
            "lower fps, use fewer values or fewer hold frames."))
    if n / anim.fps > MAX_DURATION_S:
        out.append(ValidationIssue(
            "/timeline", "duration_too_long",
            f"{src} {n} frames at {anim.fps} fps = {n / anim.fps:.1f} s; at most "
            f"{MAX_DURATION_S:g} s."))
    out.extend(poster_issues(anim, n))
    return out


def poster_issues(anim: AnimationSpec, n_frames: int | None) -> list[ValidationIssue]:
    """The poster is ``"first"``, ``"last"`` or an index in ``0..n_frames-1``.
    Negative indices are refused (use ``"last"``). ``n_frames=None``: the
    count is not known yet (a referenced frame dataset); only the form and
    sign are checked, the range is checked once the data is resolved."""
    p = anim.poster
    if isinstance(p, str):
        if p not in ("first", "last"):
            return [ValidationIssue("/poster", "invalid_poster",
                                    "poster is 'first', 'last' or a frame index.")]
        return []
    if isinstance(p, bool) or not isinstance(p, int) or p < 0:
        return [ValidationIssue("/poster", "invalid_poster",
                                "poster frame must be a non-negative integer index (negative "
                                "indices are not accepted; use 'last'), 'first' or 'last'.")]
    if n_frames is not None and p >= n_frames:
        return [ValidationIssue("/poster", "invalid_poster",
                                f"poster frame {p} does not exist; frames are 0..{n_frames - 1}.")]
    return []


def frame_mapping(anim: AnimationSpec, n_states: int) -> list[int]:
    """State shown in each output frame. Call only after the guards passed."""
    repeat = repeat_of(anim)
    frames = [0] * anim.hold_start
    for i in range(n_states):
        frames.extend([i] * repeat)
    frames.extend([n_states - 1] * anim.hold_end)
    return frames


def poster_index(anim: AnimationSpec, n_frames: int) -> int:
    """The poster's frame index; :func:`poster_issues` must be empty."""
    if anim.poster == "first":
        return 0
    if anim.poster == "last":
        return n_frames - 1
    return int(anim.poster)
