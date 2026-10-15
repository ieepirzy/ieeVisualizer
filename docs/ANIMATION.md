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

`render_animation`, `export_animation` and the adapter's `render`/`plan`
take `deadline=` (seconds, or a `Deadline` to share; default
`DEFAULT_DEADLINE_S` = 60). It is checked cooperatively between pipeline
phases (validate, prepare, plan, parse, limits, draw, encode) and before
every state and every encoded frame inside them; an export uses ONE deadline
for every format and the poster. On expiry the work stops at the next check,
ffmpeg is killed and reaped, temporary files are removed and
`DeadlineExceeded` (an `AnimationError`) is raised with one issue
`render_deadline_exceeded` at the root path (`""`, printed as `/`) and
`stats`: `phase`, `elapsed_s`, `deadline_s` and progress counters
(`states_built`, `states_limited`, `states_drawn`, `frames_encoded`).

**Slack.** The deadline is overrun by at most one uninterruptible step:

* validating the spec once (about 3.7 us per inline data cell; at most
  `MAX_ANIMATION_CELLS` = 1 M cells, `data_too_large`, so about 4 s);
* one expression build (capped as above, about 0.55 s) or one template state
  build (two expressions plus sampling, about 1.2 s): expressions are built
  one at a time in a `parse` phase and cached for the render, so a state's
  draw only looks them up;
* drawing one state, which the estimate bounds on its model (a state the
  estimate under-counts can still overrun by its own draw time);
* the final GIF write, which is not started unless the time left covers
  `GIF_WRITE_S_PER_PIXEL` x all frame pixels (at most 2.9 s at 120 M px,
  about 1 s for chart-like frames);
* stopping an MP4 encode: ffmpeg is killed at once; a frame being drawn in
  the writer thread is waited for at most `STOP_JOIN_S` = 2 s.

`tests/animation/test_anim_deadline.py` runs reviewer inputs (a 0.5 s
expression over 120 states as a function sweep, a template sweep, an MP4 and
a GIF + MP4 export) with a 2 s deadline: they stop after 2.03-2.39 s here
(the test allows 4.5 s for slower runners).

**Per-step work** is what the state changes (see `frames.py` and
`mpl.py`): undrawn datasets are dropped once, a mark sweep shares the data,
each expression is built once per render, a frame column reads each row's
frame value once, and the poster is kept as drawn (an export plans and
draws nothing again for it).

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
(which counts its frame's rows) on y. Category axes and polar/3D figures
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
install it, its render tests are skipped there). Registered only if `import
manim` succeeds and **never selected automatically** (`auto_select=False`):
its frames do not look like the Matplotlib frames or a still, so it is used
only when named (`AnimationRequest(adapter="manim")`). Supported, nothing else:

* a `parameter_sweep` of a symbolic one-variable `function` mark in
  `cartesian2d` (every layer such a mark, no annotations, linear axes): the
  curves morph on Manim `Axes`. Only the target layer's parameter is swept;
  another layer's parameter of the same name keeps its value. A curve is
  cut where it leaves the axis range (never clamped onto the edge); a log
  axis is unmet, not drawn linear;
* a `parameter_sweep` of `vector_field_2d` in expression mode: an
  `ArrowVectorField` on the template's grid with ONE length scale for the
  whole clip (largest magnitude = 0.9 grid cells).

Everything else is reported by `ManimAdapter().unmet(...)`, never
approximated. Labels use `Text` (no LaTeX). The background and text colours
come from the render's theme (`unknown_theme` warning otherwise). Its cost
model is per output frame (`manim_work_us`); the render runs under the
deadline, checked before every frame Manim draws and while the clip is
decoded. Manim's config is process-global, so renders are serialized
behind `RENDER_LOCK` (concurrent renders used to get another render's size,
lose files or print progress bars). One `play()` covers every output frame;
the clip is written as MP4 by Manim and decoded one frame at a time (GIF
through the same Pillow encoder; odd sizes rendered one pixel larger and
cropped back); the poster is the decoded poster frame, so an export plans and
builds nothing again for it.

## Decisions and gaps

* A declarative timeline grammar (sweep / frame column); no hook for
  arbitrary renderer scene code (untrusted executable input from agents).
* Frames are stills; `fixed` limits by default (a moving axis hides motion).
* One parameter per animation; category axes are not held fixed. A template
  sweep's value-dependent refusals surface when the state is built. The
  deadline is cooperative: one state's draw is not interrupted.
