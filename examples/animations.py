"""Animation examples: two parameter sweeps and a frame-column animation.

Run: python examples/animations.py  (writes to examples/out/, which is gitignored)

For each example ``<name>`` it writes, through
:func:`ieeVisualizer.animation.export_animation`,

    <name>.animation.gif     the animation (Matplotlib adapter, Pillow GIF)
    <name>.poster.png        the poster frame as a still
    <name>.animation.json    the canonical AnimationSpec
    <name>.manifest.json     the AnimationArtifact manifest (timeline, frame count)

The weekly bookings are SYNTHETIC (a fixed formula, one week deliberately
missing); they are labelled so in their provenance and are not Muutto365 data.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ieeVisualizer.animation import (
    AnimationRequest,
    AnimationSpec,
    FrameColumn,
    ParameterSweep,
    export_animation,
)
from ieeVisualizer.spec import (
    Axis,
    DataSnapshot,
    FigureSpec,
    FunctionMark,
    Legend,
    LineMark,
    Provenance,
)

OUT = Path(__file__).parent / "out"
REQUEST = AnimationRequest(width=480, height=320, units="px", dpi=80)
CLOCK = dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)


class FlatWriter:
    """Stores ``<out>/<example>.<file>`` instead of ``<out>/<artifact_id>/<file>``."""

    def __init__(self, out: Path, name: str) -> None:
        self.out, self.name = Path(out), name

    def write(self, artifact_id: str, name: str, content: bytes, media_type: str) -> str:
        path = self.out / f"{self.name}.{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)


def sine_phase() -> AnimationSpec:
    fig = FigureSpec(
        title="sin(x + phase)",
        layers=[FunctionMark(expression="sin(x + phase)", variables=["x"],
                             domain={"x": [0.0, 6.283185307179586]}, samples=200,
                             parameters={"phase": 0.0}, role="reference")],
        axes={"x": Axis(label="x", unit="rad"), "y": Axis(label="sin(x + phase)")},
        legend=Legend(show=False),
    )
    return AnimationSpec(
        figure=fig, fps=12, hold_end=6,
        timeline=ParameterSweep(parameter="phase", start=0.0, end=6.283185307179586,
                                duration=2.0))


def damped_oscillator() -> AnimationSpec:
    fig = FigureSpec(
        title="Damped oscillator x(t) = exp(-zeta*t) cos(3t)",
        layers=[FunctionMark(expression="exp(-zeta*t)*cos(3*t)", variables=["t"],
                             domain={"t": [0.0, 10.0]}, samples=400,
                             parameters={"zeta": 0.05}, role="reference")],
        axes={"x": Axis(label="t", unit="s"), "y": Axis(label="x(t)", unit="m")},
        caption="Computed from the formula; not a measurement.",
        legend=Legend(show=False),
    )
    return AnimationSpec(
        figure=fig, fps=12, hold_start=4, hold_end=8, poster="last",
        timeline=ParameterSweep(parameter="zeta", start=0.05, end=1.0, duration=2.5,
                                easing="smooth"))


def weekly_bookings_data() -> DataSnapshot:
    weeks, north, south = [], [], []
    for i in range(12):
        weeks.append((dt.date(2026, 6, 1) + dt.timedelta(weeks=i)).isoformat())
        north.append(40 + 3 * i + (5 if i % 4 == 1 else 0))
        south.append(None if i == 6 else 25 + 2 * i - (4 if i % 3 == 0 else 0))
    return DataSnapshot.from_columns(
        {"week": weeks * 2, "region": ["north"] * 12 + ["south"] * 12,
         "bookings": north + south},
        units={"bookings": "bookings"},
        dtypes={"week": "datetime", "region": "str", "bookings": "int"},
        provenance=Provenance(
            source="synthetic",
            notes="SYNTHETIC example data from a fixed formula in examples/animations.py; "
                  "week 2026-07-13 of 'south' is deliberately missing. Not real bookings."))


def weekly_bookings() -> AnimationSpec:
    data = weekly_bookings_data()
    data.columns["bookings"].aggregation = "count per ISO week"
    fig = FigureSpec(
        title="Weekly bookings (synthetic)",
        datasets={"bookings": data},
        layers=[LineMark(data="bookings", encoding={"x": "week", "y": "bookings",
                                                    "series": "region"},
                         style={"marker": "o"})],
        axes={"x": Axis(label="Week"), "y": Axis(label="Bookings", limits=[0, None])},
        caption="Synthetic data. The missing south week is a gap, not zero.",
        legend=Legend(position="upper-left"),  # fixed: "auto" may move between frames
    )
    return AnimationSpec(
        figure=fig, fps=4, hold_end=6,
        timeline=FrameColumn(dataset="bookings", column="week", display="cumulative"))


EXAMPLES = {
    "sine_phase": sine_phase,
    "damped_oscillator": damped_oscillator,
    "weekly_bookings": weekly_bookings,
}


def main(out: Path | None = None) -> dict:
    out = Path(out or OUT)
    out.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, build in EXAMPLES.items():
        artifacts[name] = export_animation(build(), ["gif"], FlatWriter(out, name),
                                           request=REQUEST, clock=lambda: CLOCK, name=name)
        a = artifacts[name]
        print(f"{name}: {a.frame_count} frames, {a.duration_s:.2f} s -> "
              f"{out / (name + '.animation.gif')}")
    return artifacts


if __name__ == "__main__":
    main()
