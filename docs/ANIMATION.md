# Animations (`ieeVisualizer.animation`)

An **AnimationSpec** is a [FigureSpec](SPEC.md) that changes along a
**timeline**. It is the "time-varying sequence" of
[VISION_AND_ARCHITECTURE.md](VISION_AND_ARCHITECTURE.md) section 7, kept
separate from a snapshot (a FigureSpec) and from a live stream (not modelled
yet). The spec is renderer-neutral JSON; importing the package imports
neither matplotlib nor manim.

```python
from ieeVisualizer.animation import (AnimationSpec, ParameterSweep, FrameColumn,
                                     AnimationRequest, render_animation, export_animation)
from ieeVisualizer.artifacts import LocalDirectoryWriter

anim = AnimationSpec(figure=fig, fps=12, hold_end=6,
                     timeline=ParameterSweep(parameter="phase", start=0, end=6.283, duration=2))
result = render_animation(anim, AnimationRequest(format="gif", width=480, height=320,
                                                 units="px"))
artifact = export_animation(anim, ["gif"], LocalDirectoryWriter("out"))
```

`python examples/animations.py` writes three examples to `examples/out/`
(a sine phase sweep, a damped-oscillator damping sweep and a weekly-bookings
frame-column animation over **synthetic** data).

## The spec

Serialized form (`to_json` is canonical: sorted keys, numbers as floats;
`animation_hash` is the SHA-256 of it, inline data included):

```json
{
  "kind": "animation", "schema_version": "1.0",
  "figure": { "...": "a FigureSpec, schema 1.0" },
  "timeline": {"type": "parameter_sweep", "parameter": "phase",
               "target": {"kind": "mark", "layer": 0},
               "start": 0.0, "end": 6.283, "duration": 2.0, "easing": "linear"},
  "fps": 12, "hold_start": 0, "hold_end": 6,
  "axis_limits": "fixed", "label_frames": true, "poster": "last", "loop": true,
  "metadata": {}
}
```

| Field | Meaning |
|---|---|
| `timeline` | `parameter_sweep` or `frame_column` (below). |
| `figure` | The base FigureSpec. Required, except for a template sweep where it must be absent (the template payload is the source). |
| `fps` | 1..60 frames per second. |
| `hold_start`, `hold_end` | Extra frames repeating the first/last state. |
| `axis_limits` | `fixed` (default): every axis keeps one range for the whole animation; `auto`: each frame scales itself. Limits set in the figure are kept; only open ends are filled. |
| `label_frames` | Append the state to the title: `phase = 1.571`, `week: through 2026-07-13`. |
| `poster` | `first`, `last` or a frame index `0..frame_count-1`: the frame exported as a still. Negative indices are refused (`invalid_poster`; use `last`), not read Python-style. |
| `loop` | GIF loops forever when true (MP4 has no loop flag; a warning says so). |

### Parameter sweep

* **Mark target** `{"kind": "mark", "layer": i}`: varies `parameters[name]` of
  a symbolic `function` mark. The figure keeps a base value (used by stills of
  the figure itself); each state replaces only that value.
* **Template target** `{"kind": "template", "name", "version", "params",
  "data"}`: varies a `number` parameter of a registered template, or
  `map.key` of a `number_map` parameter (e.g. `constants.k` of
  `vector_field_2d`). The exact template version is required. The figure is
  rebuilt by the template for every value, so its data (e.g. the sampled field)
  is recomputed honestly per frame.

Values are either listed (`values`, at least two, one state each) or
interpolated: `start`, `end`, `duration` seconds give
`floor(duration * fps + 0.5)` states (halves round up, so 0.25 s at 10 fps is
3 states; at least 2) including both endpoints, eased `linear` or `smooth`
(smoothstep).

### Frame column

`{"type": "frame_column", "dataset", "column", "display", "window",
"frames_per_value"}`: one state per distinct non-null value of the column.
Numbers and datetimes are ordered ascending (datetimes by instant, so
`10:00+03:00` and `09:00+02:00` are one frame); strings and booleans by first
appearance. State *k* shows the rows whose value is the *k*-th
(`current`), any of the first *k* (`cumulative`) or the last `window` values
(`window`). Other datasets are drawn unchanged in every frame. Rows with no
frame value appear in no frame (`frame_value_missing` warning). A `DatasetRef`
is resolved once per `render_animation` call, through `resolver=`, before
frames are cut; the frame, duration and poster guards are checked against the
resolved count before any frame is built (validation can only check the hold
frames plus one state, and the poster's sign, until then).

## Validation

`validate_animation(anim)` never raises; it returns
`ValidationIssue(path, code, message, severity)` with JSON pointers into the
serialized AnimationSpec. Every FigureSpec issue is included under `/figure`.
Animation codes: `figure_missing`, `figure_not_allowed`, `invalid_value`,
`sweep_source_missing`, `sweep_source_conflict`, `too_few_values`,
`unknown_layer`, `not_a_symbolic_function`, `unknown_parameter`,
`unknown_template`, `unsupported_parameter`, `unknown_dataset`,
`unknown_column`, `frame_dataset_unused`, `no_frames`, `too_many_frames`,
`duration_too_long`, `invalid_poster`; warnings `frame_value_missing` and
`quiver_rescaled_per_frame` (a quiver without `scale` rescales its arrows in
every frame, hiding changes in magnitude). `from_dict`/`from_json` raise
`SpecParseError` for structural problems (`not_an_animation`,
`unsupported_schema_version`, unknown keys, NaN/Infinity).

A template sweep runs the template's own check at **every** sweep value (at
most 300, after the frame guard), not only the ends. An issue that does not
depend on the value is reported once under `/timeline/target/...`; one raised
at some values is reported at the first, `/timeline/values/<i>` (`/timeline`
for an interpolated sweep, with the state index), naming the template path and
how many other values fail. If a template's `build()` still refuses a value,
the render reports it at the same sweep-value path.

**Guards:** at most 300 output frames, 60 s and 60 fps (validation errors
`too_many_frames`, `duration_too_long`), and at most 120 million pixels as
frame area x output frames, hold and repeated frames included
(`animation_too_large`). Frame counts are computed arithmetically from
`duration`, `fps`, `len(values)`, the number of distinct frame values,
`frames_per_value` and the holds, before any list of values, states or frames
is built, so an oversized spec is refused in milliseconds. They are checked
in `validate_animation`, again in `plan()`, and, with the pixel guard, by
`render_animation` for every adapter before its `render` is called
(`base.enforce_limits`); each built-in adapter also checks them itself when
called directly.

## Matplotlib adapter (`matplotlib-animation`)

Each distinct state is rendered by the static `MatplotlibAdapter` as a PNG
(same validation, theme, determinism settings and drawing code as a still)
and decoded, so **frame *k* equals, pixel for pixel, the static render of its
frame spec**; `MatplotlibAnimationAdapter().frame_specs(anim)` returns those
specs. `matplotlib.animation` is not used: it mutates one figure between
frames, which would make a frame differ from the still of that state. Hold
frames and `frames_per_value` repeat a state; they are not re-rendered.

**Fixed axis limits** are found in a first pass: for a sweep, each state is
built (not exported) and the union of matplotlib's automatic x/y ranges is
written into every frame spec's `limits`; for a frame column, the range of the
whole figure (all frames' rows) is used, because autoscaling a lone point or
date is degenerate (matplotlib widens a single date to about two years).
Time axes get ISO-8601 limits. Category axes and polar/3D figures keep
automatic limits, with an `axis_limits_auto` warning.

**Missing data stays missing.** A null value inside a frame is a gap in the
line, exactly as in a still (`null_values_omitted`). A layer with no complete
row in a frame is left out of that frame (`frame_layer_empty`), never drawn as
zero; a frame in which no layer has data is drawn as an explicit "no data"
frame carrying the state label (`frame_no_data`), not as an empty chart.

**GIF** is written with Pillow (a matplotlib dependency, so always present):
median-cut quantization to 256 colours without dithering, no timestamps.
Bytes are identical across runs and processes (tested in-process; also
checked across two processes here). GIF delays are stored in 10 ms steps, so
e.g. 12 fps plays at 80 ms per frame; `frame_duration_ms` reports the stored
delay and `frame_timing_rounded` says so. Pillow merges identical
consecutive frames into one longer frame when writing (hold frames), so a
decoder may count fewer frames than `frame_count`; the total duration is
unchanged.

**MP4** needs an `ffmpeg` executable (`$IEEVISUALIZER_FFMPEG` or `ffmpeg` on
`PATH`). `capabilities.output_formats` lists `mp4` only when one is found and
`notes` says why it is missing; requesting MP4 without it is refused with
`format_unavailable` (adapter) or `CapabilityError` (selection), never
silently turned into a GIF. Frames are streamed to ffmpeg's stdin one at a
time. Encoding: libx264 (`-threads 1`, bitexact flags,
metadata stripped, yuv420p, odd sizes padded by one pixel) or MPEG-4 part 2
without libx264. With ffmpeg 6.1.1 here the bytes were identical across two
processes; this is not promised for other ffmpeg builds.

## Export

`export_animation(anim, formats, writer, *, resolver, clock, request, name,
registry)` renders every format first, then writes `animation.<fmt>`,
`poster.png`, `animation.json` (canonical spec) and `manifest.json` last; a
failure writes nothing. The poster is frame `poster_frame` of the first
format's clip, recorded with the adapter that drew it. For the Matplotlib
adapter it is the static render of the poster state (with the fixed limits),
which equals that frame. For Manim it is that frame decoded from the Manim
clip (`poster.adapter` is `manim`, warning `poster_from_clip`), never a
Matplotlib still standing in for a Manim frame. The `AnimationArtifact` manifest records
`artifact_id` (`<name or "anim">-<16 hex>` over the animation hash, dataset
content hashes, formats and request), `animation_hash`, `figure_spec_hash`,
the `timeline`, `fps`, `frame_count`, `state_count`, `frame_duration_ms`,
`duration_s`, `poster_frame`, every state (`value`, `label`, `empty`,
`omitted_layers`), the state shown in each frame, dataset provenance (for a
template sweep: the poster state's generated data), each variant with
adapter, request and warnings, library versions (plus Pillow, ffmpeg and
manim when used) and `created_at`.

## Capabilities and selection

`AnimationCapabilities` = a figure `Capabilities` (what one frame may contain)
+ `timelines` (`parameter_sweep:mark`, `parameter_sweep:template`,
`frame_column`) + `output_formats` + `max_frames`/`max_duration_s` +
`templates` + `notes`. `render_animation` validates, then picks the first
adapter in `default_animation_registry()` whose `unmet(anim, request)` is
empty and whose capabilities allow automatic selection
(`auto_select`); `AnimationRequest(adapter="manim")` pins one. The registry
holds the Matplotlib adapter, plus the Manim adapter only if `import manim`
succeeds (tried once, on first use). **Manim is never selected
automatically** (`auto_select=False`): its frames look different from the
Matplotlib frames and from a still of the same state, so a request that does
not name it never gets it, and a missing ffmpeg makes an MP4 request fail
with `CapabilityError` instead of silently switching renderer.

## Manim adapter (optional, `pip install -e ".[manim]"`)

**Status: verified in this environment** (manim 0.22.0, Cairo renderer,
Python 3.13, Ubuntu 24.04): its tests (`tests/animation/test_anim_manim.py`)
render MP4 and GIF clips and pass; frames were extracted and inspected. CI
does not install manim; those tests are skipped there.

Supported, and nothing else:

* `parameter_sweep` of a symbolic one-variable `function` mark in
  `cartesian2d`, when every layer is such a mark and there are no
  annotations: the curves morph on Manim `Axes` while a `ValueTracker` moves
  through the sweep values. Non-finite samples are gaps.
* `parameter_sweep` of the `vector_field_2d` template in expression mode
  (`u`, `v`, no data): an `ArrowVectorField` on a grid identical to the
  template's, with arrow length proportional to magnitude and ONE scale for
  the whole clip (largest magnitude over all frames = 0.9 grid cells), so a
  growing field visibly grows. Arrows are one colour (no unlabelled colour
  scale).

Frame columns, data layers, annotations, legends, 3D, polar and other
templates are reported as unmet (`ManimAdapter().unmet(...)`) and never
approximated. Labels use `Text` (Pango), never `MathTex`: no LaTeX is
needed. Tick labels show only each axis' end values. `style` keys other than
`color`/`linewidth`, roles and legends produce warnings. Manim frames do not
look like the Matplotlib frames; they are not meant to be pixel-equal.

Timing: one `play()` covers every output frame (run time = frames - 0.5,
so float rounding can neither add nor drop a frame; a rate function holds the
first/last state for the hold frames). Separate `wait()` segments lost frames
when Manim concatenated them, so they are not used. The clip is written as
MP4 by Manim (through PyAV, not the system ffmpeg); GIF is made by decoding
those frames one at a time (never all held as RGB) straight into the same
Pillow encoder as above, because Manim's own GIF palette banded colours
badly; for MP4 the frames are only counted. A clip whose decoded frame count
differs from the plan is refused (`frame_count_mismatch`). Both outputs were
byte-identical across two processes here. The result reports timing and the
frame mapping exactly as the Matplotlib adapter does: `frame_duration_ms` is
the stored GIF delay (70 ms at 15 fps, with `frame_timing_rounded`) or
`1000 / fps` for MP4 (with `loop_ignored`), `frames` maps every output frame
to its state, and `poster_frame` is the validated poster index.

### Installing Manim: what happened here

* `python3 -m venv /home/user/.venv-manim && pip install manim` (manim 0.22.0)
  **failed** at first: no `manimpango` 0.7.0 wheel for this platform, and its
  source build needs `pangocairo >= 1.30.0` via `pkg-config`, which was absent
  (`RequiredDependencyException: pangocairo >= 1.30.0 is required`).
* Already present: `libcairo2`, `libcairo2-dev`, `libpango-1.0-0`,
  `libpangocairo-1.0-0`, `pkg-config`, `ffmpeg` 6.1.1. Not present: LaTeX.
* `apt-get update && apt-get install libpango1.0-dev` (plus its dependencies
  such as `libharfbuzz-dev`, `libthai-dev`), then `pip install manim`
  **succeeded**, building `manimpango` 0.7.0 and `pycairo` 1.29.2 from source.
  It also pulled PyAV 19, moderngl, scipy, skia-pathops and others.
* A Cairo-renderer scene with `Axes`, a plotted curve, `Text` and a
  `Transform` rendered at low quality without LaTeX.

## Decisions

* **Timeline grammar, no Manim-native escape hatch (yet).** Section 10 of the
  vision leaves "animation timeline grammar vs Manim-native scene escape
  hatch" open. This slice ships a small declarative grammar (sweep / frame
  column) that every adapter reads, and no hook for arbitrary Manim scene code:
  such code would be untrusted executable input from agents, needs sandboxing,
  and would not be renderer-neutral. Revisit with a concrete use case.
* Frames are stills: correctness of an animation reduces to correctness of
  the static renderer plus the frame plan.
* `fixed` axis limits by default, because a moving axis makes motion
  unreadable; `auto` remains available.
* No new hooks in `ieeVisualizer/render/mpl/`: the adapter uses the public
  `MatplotlibAdapter.render/build_figure`, plus two internals of that package
  read-only (`render_context` and `_figsize` for the "no data" frame,
  `data.infer_axes` for axis kinds).

## Gaps and follow-ups

* No camera moves, transitions between unrelated figures, annotation
  timelines or multi-parameter sweeps; one parameter per animation.
* Category axes are not held fixed, and a category's position can change
  between `current` frames; a legend at `position: "auto"` may move between
  frames (set a position).
* Fixed limits cost one extra (unexported) build per state.
* Manim: no frame columns, data layers, annotations or legend; tick labels
  only at the axis ends; title from the figure/template only.
* HTML/interactive playback, live streams and a Manim scene escape hatch are
  out of scope here.
