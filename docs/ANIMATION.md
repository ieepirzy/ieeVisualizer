# Animations (`ieeVisualizer.animation`)

An **AnimationSpec** is a [FigureSpec](SPEC.md) that changes along a
**timeline**. It is the "time-varying sequence" of
[VISION_AND_ARCHITECTURE.md](VISION_AND_ARCHITECTURE.md) section 7, kept
separate from a snapshot (a FigureSpec) and from a live stream (not modelled
yet). The spec is renderer-neutral JSON; importing the package does not
import matplotlib.

```python
from ieeVisualizer.animation import (AnimationSpec, ParameterSweep, FrameColumn,
                                     AnimationRequest, render_animation, export_animation)
from ieeVisualizer.artifacts import LocalDirectoryWriter

anim = AnimationSpec(figure=fig, fps=12, hold_end=6,
                     timeline=ParameterSweep(parameter="phase", start=0, end=6.283, duration=2))
result = render_animation(anim, AnimationRequest(format="gif", width=480, height=320,
                                                 units="px"))
artifact = export_animation(anim, ["gif"], LocalDirectoryWriter("out"), deadline=60)
```

## The spec

`to_json` is canonical (sorted keys, numbers as floats); `animation_hash` is
its SHA-256, inline data included. Fields:

| Field | Meaning |
|---|---|
| `timeline` | `parameter_sweep` or `frame_column` (below). |
| `figure` | The base FigureSpec. Required, except for a template sweep where it must be absent (the template payload is the source). |
| `fps` | 1..60 frames per second. |
| `hold_start`, `hold_end` | Extra frames repeating the first/last state. |
| `axis_limits` | `fixed` (default): every axis keeps one range for the whole animation; `auto`: each frame scales itself. Limits set in the figure are kept; only open ends are filled. |
| `label_frames` | Append the state to the title: `phase = 1.571`, `week: through 2026-07-13`. Numbers get 4 significant digits. Only the values of a group that would read the same are widened: up to 3 more digits (`a = 1.0001`, `a = 1.0002`), or every digit of an integer (`12345678`, `12345679`); every other label keeps its exact 4-digit text. A group that still reads the same (`1+1e-12`, `1+2e-12`) keeps its texts and gives one `frame_labels_ambiguous` warning naming the states (labels tell values apart down to about 1 part in 10^6 of their size). The label ends the title: when the title runs past the frame's edge, Matplotlib renders warn `frame_label_clipped` (the label is what gets cut off); Manim shrinks the title to fit. |
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

Values are either listed (`values`, at least two) or interpolated: `start`,
`end`, `duration` seconds give `duration * fps` states
rounded half up, computed in decimal on the number as written (0.25 s at 10 fps is 3 states,
4.1 s at 15 fps is 62; at least 2), endpoints included, eased `linear` or
`smooth`.

### Frame column

`{"type": "frame_column", "dataset", "column", "display", "window",
"frames_per_value"}`: one state per distinct non-null value of the column.
Numbers and datetimes are ordered ascending (datetimes by instant, so
`10:00+03:00` and `09:00+02:00` are one frame); strings and booleans by first
appearance. State *k* shows the rows whose value is the *k*-th
(`current`), any of the first *k* (`cumulative`) or the last `window` values
(`window`). Other datasets are drawn unchanged in every frame. Rows with no
frame value appear in no frame (`frame_value_missing` warning). A `DatasetRef`
is resolved once per `render_animation` (or `export_animation`) call, through
`resolver=` (equal references are one dataset), before
frames are cut; the frame, duration and poster guards are checked against the
resolved count before any frame is built (validation can only check the hold
frames plus one state, and the poster's sign, until then).

## Validation

`validate_animation(anim)` never raises; it returns `ValidationIssue`s with
JSON pointers into the serialized AnimationSpec (FigureSpec issues under
`/figure`); the codes are listed in `ieeVisualizer/animation/validate.py`.
`from_dict`/`from_json` raise `SpecParseError` for structural problems. An
animated figure has at most `MAX_ANIMATION_ANNOTATIONS` = 200 annotations;
a quiver without `scale` warns (`quiver_rescaled_per_frame`).

Per-state work no count models is bounded here, so no render step runs
long: drawn text (titles, captions, axis labels and units, legend title,
annotations, layer names, string data on a drawn channel or frame column) is
at most 2000 characters (`text_too_long`), and every evaluated expression
(function marks, a template's `u`/`v`) has a `minmax_cost` (sum over its
`Min`/`Max` groups, after SymPy's flattening, of the argument count squared)
of at most 128 (`expression_too_complex`): SymPy compares those arguments
pairwise on every build (a `Max` of 59 terms: 5 s; of 11: at most 0.55 s).

A template sweep is checked in full (the template's own `check()`, with its
SymPy dry-run) at its first value only, after the work estimate. A value the
template refuses elsewhere is reported when that state is built, under the
deadline, at `/timeline/values/<i>` (`/timeline` for an interpolated sweep,
with the state index), naming the template path.

**Guards** (early refusals, from counts, before anything is built): at most
300 output frames, 60 s and 60 fps (`too_many_frames`, `duration_too_long`);
at most 3840 x 2160 px per frame and 120 M px as frame area x output frames
(`animation_too_large`); an estimated render time of at most 60 s
(`animation_too_expensive`; measured constants in
`ieeVisualizer/animation/cost.py`). **The estimate is not the guarantee**:
every dimension it does not model can make it wrong by orders of magnitude
(review rounds 2-4 found seven). The deadline is.

## Deadline

`render_animation` and `export_animation` take `deadline=` (seconds, or a
`Deadline` to share; default `DEFAULT_DEADLINE_S` = 60) and
`isolation=` (`"process"` by default where it can run;
`$IEEVISUALIZER_ANIMATION_ISOLATION` changes the default). With `"process"`
references are resolved in the caller and only the data cell cap is checked
there, from column lengths (O(columns)), before anything is serialised; the
spec is validated and hashed once, in the worker. Then the render
or export runs in a fresh worker interpreter (`ieeVisualizer.animation.isolate`);
phase and progress stream back, and at the deadline the worker's whole
process group (ffmpeg included) is killed and its temporary directory
removed. The deadline is then a wall-clock bound: **overrun at most
`ISOLATION_SLACK_S` = 1 s** (measured 5-6 ms over 1, 2 and 5 s deadlines for GIF, MP4 and GIF + MP4
exports, and for an ffmpeg that blocks the frame pipe).
Starting a worker costs about 0.9 s, inside the deadline, so a deadline of
about 1 s can expire while the worker starts; concurrent renders each pay it,
and at most `$IEEVISUALIZER_ANIMATION_WORKERS` (default: the CPU count)
workers run at once per process (a value that is not a positive integer is
`invalid_setting`), a render waiting for a slot waiting inside its own
deadline. A worker also cannot leave Matplotlib's global state
changed. The writer of an export runs in the caller, after the deadline is
checked once more.

**Where process isolation runs:** POSIX systems with a real Python
interpreter as `sys.executable`. On Windows, in a frozen application, or
with an empty or non-executable `sys.executable` the default is inline and
the result (each variant of an export) carries a `deadline_best_effort`
warning; asking for
`isolation="process"` there raises `isolation_unavailable`. Adapters,
registries and resolvers sent to the worker must be importable by module
name: one defined in a script's or notebook's `__main__` is refused
(`render_payload_not_importable`; render it with `isolation="inline"`).

**Trust boundary.** The worker runs `python -I` (the current directory and
`PYTHON*` variables are ignored) with `sys.path` set to the caller's absolute
entries and only the environment it needs (`PATH`, `HOME`, locale, font and
Matplotlib cache locations, thread counts, `IEEVISUALIZER_*`). The request
goes in pickled (the caller is the trusted side, and the worker must rebuild
its adapters); the answer comes back as length-framed JSON plus raw byte
blobs, decoded only into the result classes and an allow-list of exception
types and issue fields, so a compromised worker cannot run code in the
caller. Its output goes to a log whose tail is quoted in
`render_worker_failed`, never to the caller's stderr. A watchdog forked
before any import kills the worker's process group and removes the
temporary directory when the caller exits or is killed (SIGKILL included).

`isolation="inline"` (and calling an adapter's `render`/`plan` directly)
runs in the caller with cooperative checks only, between pipeline phases
(validate, prepare, plan, parse, limits, draw, encode) and before every
state and frame: best effort, for tests and debugging. One step that holds
the GIL (a draw, a SymPy build, the GIF write, a pipe write to an ffmpeg
that stopped reading) overruns it. A render that
ran inline for any reason (asked for, by argument or environment, or the
fallback) carries a `deadline_best_effort` warning saying why, and
`AnimationResult.isolation` is `"inline"` (`"process"` from a worker; an
export's manifest records it as `isolation`). The phase reported on expiry
is the worker's current one (a new phase is reported at once).

On expiry `DeadlineExceeded` (an `AnimationError`) is raised with one issue
`render_deadline_exceeded` at the root path (`""`, printed as `/`) and
`stats`: `phase` (the phase that was running), `elapsed_s`, `deadline_s` and
progress counters (`states_built`, `states_limited`, `states_drawn`,
`frames_encoded`).

**Up-front refusals** keep single steps short whatever the isolation: data
at most 1 M cells, inline or resolved (`data_too_large`, checked on resolved
data before it is validated), a heatmap grid (distinct x x distinct y) at
most 250,000 cells, line widths at least 0.05 pt (dash patterns scale with
the width), math text at most 6 levels deep and 40 commands
(`text_too_complex`), expressions within the Min/Max cost, text at most 2000
characters; a template's data columns are charged in the estimate.

**Per-step work** is what the state changes: undrawn datasets are dropped
once, a mark sweep shares the data, each expression is built once per
render, a frame column reads each row's frame value once, the data is
validated once per export, and the poster is kept as drawn.

**Memory.** Frames are streamed (one RGB frame exists at a time). A GIF
keeps one palette byte per output-frame pixel, which Pillow's writer copies
once; an MP4 keeps nothing. Peak RSS above the interpreter's is about
2 bytes x `MAX_TOTAL_PIXELS` + one frame being drawn (about 45 bytes per
pixel): measured +303 MB for 120 frames of 1000 x 1000
px (GIF), +635 MB for 14 frames of 3840 x 2160 px (GIF, the worst case),
+147 MB for the 120-frame MP4.

## Matplotlib adapter (`matplotlib-animation`)

Each distinct state is rendered by the static `MatplotlibAdapter` as a PNG
(same validation, theme, determinism settings and drawing code as a still)
and decoded, so **frame *k* equals, pixel for pixel, the static render of its
frame spec** (`MatplotlibAnimationAdapter().frame_specs(anim)`).
`matplotlib.animation` is not used: it mutates one figure between frames.
Hold frames and `frames_per_value` repeat a state without re-rendering it.
`render_frames()` returns every state's pixels at once, for inspection.

**Fixed axis limits** come from a first pass: for a sweep, each state is
built (not exported) and the union of matplotlib's automatic x/y ranges is
written into every frame spec's `limits`; for a frame column, the range of
the whole figure (all frames' rows) is used, because autoscaling a lone
point or date is degenerate, except where a frame's range is not its rows'
range: a heatmap takes the union of its frames on both axes, a histogram
(which counts its frame's rows) on y. Only drawn rows count: a row in no frame is excluded, and a
colour value whose position is null (so not drawn) is not in the colour range. Category axes and polar/3D figures
keep automatic limits (`axis_limits_auto`).

**Fixed colour scale.** A continuous colour scale (heatmap, or a scatter or
quiver coloured by a number) gets one `color_range` style (a new static
style key, `[lo, hi]`) in every frame: the union of the frames' values. With
`axis_limits="auto"` each frame maps its own range (`color_scale_per_frame`).

**Missing data stays missing.** A null inside a frame is a gap; a layer with
no complete row in a frame is left out of it (`frame_layer_empty`), never
drawn as zero; a frame where no layer has data is an explicit "no data"
frame with the state label, in the render's theme (`frame_no_data`).

**GIF**: Pillow, median-cut to 256 colours without dithering, no
timestamps; bytes are identical across runs. Delays are stored in 10 ms
steps (`frame_timing_rounded`); Pillow merges identical consecutive frames,
so a decoder may count fewer frames than `frame_count` (same duration).

**MP4** needs `ffmpeg` (`$IEEVISUALIZER_FFMPEG` or on `PATH`); without it
`mp4` is not in `capabilities.output_formats` and a request is refused
(`format_unavailable` / `CapabilityError`), never turned into a GIF. Frames
are streamed to ffmpeg's stdin under the deadline (its `phase` says whether
a frame was being drawn, ffmpeg had stopped reading, or had every frame but
did not finish); ffmpeg failing or exiting 0 without a file is
`encoder_failed`. libx264 (`-threads 1`, bitexact, yuv420p, odd sizes
padded: `frame_size_padded`) or MPEG-4 part 2; byte reproducibility depends
on the ffmpeg build and is not promised.

## Export

`export_animation(anim, formats, writer, *, resolver, clock, request, name,
registry, deadline)` resolves each reference once for the whole export and
renders every format under one deadline (the Matplotlib adapter draws every
state once and streams the frames to the GIF and MP4 encoders together),
then writes `animation.<fmt>`, `poster.png`, `animation.json` and
`manifest.json` last; a failure, a deadline included, writes nothing. The
poster is frame `poster_frame` as the adapter drew it (for Matplotlib, the
poster state's still), recorded with that adapter. The manifest
(`AnimationArtifact`) records the artifact id, `animation_hash`,
`figure_spec_hash`, timeline, frame and state counts, timing, every state
and the state of every frame, dataset provenance (for a template sweep, the
poster state's generated data), each variant with adapter, request and
warnings, library versions and `created_at`.

## Tests

`python -m pytest` excludes tests marked `slow` (`addopts` in
`pyproject.toml`; CI runs the same command). They render real animations to
calibrate the cost estimate; run them with `python -m pytest -m slow`.

## Manim adapter (optional, `pip install -e ".[manim]"`)

Verified here with manim 0.22.0 (Cairo renderer, Python 3.13; CI does not
install it, its render tests are skipped there). Registered when manim is
installed (found without importing it; the import is tried, once, only when
the adapter is asked for, and failing it is unmet `manim is not importable:
<reason>`) and **never selected automatically**: its frames do not look like
the Matplotlib frames, so it is used only when named
(`AnimationRequest(adapter="manim")`). Supported, nothing else:

* a `parameter_sweep` of symbolic one-variable `function` marks in
  `cartesian2d` with linear axes. Only the target layer's parameter is swept;
  a curve is cut where it leaves the axis range, never clamped; more than
  1,000 pieces per frame is `curve_too_fragmented`;
* a `parameter_sweep` of `vector_field_2d` in expression mode, sweeping
  `constants.<name>` or `scale`: arrows with the template's `scale`, or ONE
  length scale for the whole clip, or (`normalize`) one length.

**Support is an allowlist** (`manim_adapter.DRAWN`): any spec field or
template parameter set away from its default that Manim does not draw, any
parameter Manim cannot sweep, `axis_limits` other than `fixed`, a legend
(several layers with `legend.show`), the names of several layers and math
text (`$...$`, drawn literally by Manim's `Text`) are reported by
`ManimAdapter().unmet(...)` and refused (the coverage test runs on a one-layer
and a two-layer spec); a state where a layer has no finite sample is
`empty_series`, as in Matplotlib; `test_anim_manim.py`
sets every field of every spec class and template parameter and requires
each to be drawn or unmet, and checks that allowlisting an undrawn parameter
(`vector_unit`) makes that test fail. Drawn by Matplotlib's rules: the title
(with exactly the state's label when `label_frames`), the wrapped caption,
axis titles (x: the variable; y: the layer name or expression for one
layer, none from a layer's text for several; `label (unit)` without
repeating the unit; an axis spanning less than 1e-3 drawn in units of its
power of ten, `x (×1e-7)`, with an offset when far from 0), the requested limits exactly, the
theme's background, text, series colours (assigned as Matplotlib does:
by name, else expression; an explicit colour takes none from the cycle;
alpha kept) and reference alpha (a reference
vector field is translucent), `color`/`linewidth` read by the Matplotlib
style parser, and the same `nonfinite_samples_omitted` /
`null_values_omitted` warnings.

**Layout.** Text is drawn at its point size at the request's dpi, as in
Matplotlib; a text too long for its band is shrunk, never below 7 pt (the
frame is `frame_too_small` instead); every text has its own band, axis
titles are never longer than the plot (a test checks, over wide, short, portrait and high-dpi frames,
that no text bounding box intersects another, the plot or the axes, and
that every arrow is inside the plot); the plot gets the rest. Arrow tails
are on the grid points; a vector field's axes are the box of the grid and
every arrow tip, tick-labelled at their ends; the aspect is free, as in
Matplotlib (an arrow is `(u, v)` in data units on each axis). Tick labels
get the digits that tell the ends apart; a tiny span far from 0 is drawn
with an offset (`x (×1e-7 + 1.0000001)`). Series of a wrapped colour cycle
are dashed like Matplotlib's later rounds. The layout checks the deadline
between its steps. A frame that leaves less
than 1.2 x 0.8 in for the plot is `frame_too_small` (use a larger size or a
lower dpi). Known differences: axes cross at the origin, only end values are
tick-labelled.

The render runs in the deadline-killed worker by default; Manim's config is
process-global, so inline renders are serialized behind `RENDER_LOCK`,
acquired within the deadline. Manim's camera draws each output frame (a
held state once), streamed into the Matplotlib adapter's encoders (Pillow
GIF, ffmpeg MP4; MP4 therefore needs ffmpeg). Manim never writes a movie:
its threaded encoder gave different frames for the same spec in one
process, and the lossy round trip blurred GIFs and posters; the poster is
the drawn frame.

## Decisions and gaps

* A declarative timeline grammar (sweep / frame column); no hook for
  arbitrary renderer scene code (untrusted executable input from agents).
* Frames are stills; `fixed` limits by default (a moving axis hides motion).
* One parameter per animation; category axes are not held fixed. A template
  sweep's value-dependent refusals surface when the state is built. The
  deadline is cooperative: one state's draw is not interrupted.
