# Themes (`ieeVisualizer.themes`)

A theme is a corporate visual identity as **data**: palette, colour maps,
text and grid colours, type, line widths, how forecasts and references are
drawn, the number/date locale and figure sizes per delivery target. Themes
are JSON-serializable, content-hashed, versioned, and checked for
accessibility before they can be registered. Importing the package does not
import matplotlib.

```python
from ieeVisualizer import themes
from ieeVisualizer.render import render

themes.list_themes()                       # name, version, versions, title, description, pending
t = themes.get("report-light")             # latest; "name@1.0.0" pins a version
t.record()                                 # {"name", "version", "sha256"}
t.to_json(); themes.Theme.from_json(...)   # round-trips; sha256 is over the canonical JSON
themes.describe("dashboard-dark")              # theme + hash + accessibility report
spec.theme = "report-light"                # or RenderRequest(theme=...) to override
render(spec, t.request("email", "png"))    # 1200 x 750 px; "slide" = 1920 x 1080 px
themes.format_quantity(1234.5, "EUR", t.locale)   # '1 234,50 €' (no-break spaces)
```

## Resolution and provenance

`FigureSpec.theme` (or `RenderRequest.theme`, which overrides it) is
`"name"` (highest registered version) or `"name@version"`. An unknown name
renders with `default` and an `unknown_theme` warning, as before; it is not
an error (see "Breaking changes" for the consequence for renamed themes).

`RenderResult.theme` is the resolved theme's `{"name", "version",
"sha256"}`. `export()` stores the full theme as `theme.json` next to the
spec, records `manifest.theme = {"name", "version", "sha256", "file"}`, and
includes the record in the artifact id, so a new theme version gives a new
id and a historical figure can be re-rendered from its own `theme.json`
after the registered theme moved on (`register(Theme.from_json(...))` under
its version, or a separate registry).

## Fields

`None` means "the renderer's default"; a theme only claims (and is only
checked for) what it declares. `default` declares nothing.

| Field | Meaning (matplotlib mapping) |
|---|---|
| `palette` | series colours in order of use (`axes.prop_cycle`); more series than colours adds one `colour_cycle_wrapped` warning (see RENDERING.md, "Many series") |
| `sequential`, `diverging` | colour stops (low to high) or a colormap name; default colour map of heatmaps, continuous scatter/quiver colour and surfaces; a style `colormap` of `"sequential"`/`"diverging"` names the theme's map |
| `background`, `foreground`, `muted`, `grid` | figure/axes ground; text, ticks; secondary text (caption, "no data", annotations, value labels) and spines; grid lines and legend frame |
| `signal` | reserved emphasis colour ("needs a person"); refused if it is also a palette colour |
| `semantic` | status colours by meaning (`positive`, `negative`, `attention`); data for callers, not series colours |
| `typography` | font family list and sizes (`size`, `title`, `label`, `tick`, `legend`, `caption`) |
| `line_width`, `grid_show`, `grid_alpha`, `grid_width`, `grid_below`, `spines` | strokes and frame |
| `roles` | `forecast_linestyle` (dashed/dotted/dashdot, never solid), `forecast_hatch`, `forecast_fill_alpha`, `forecast_line_alpha` (a forecast line's opacity factor, default 0.75), `reference_color`, `reference_alpha`, `reference_fill_alpha`, `reference_linewidth` |
| `locale` | `NumberLocale`: decimal and group separators, minus sign, percent and currency layout, six concise date patterns |
| `targets` | `{"email": FigureTarget(width, height, dpi), "slide": ...}` |
| `provenance`, `pending` | where every value came from; slots still awaiting an official source |

With a `locale`, numeric axes use it for tick labels (no offset or `1e6`
multiplier), date axes use its patterns, and bar value labels use it. fi-FI:
`1 234,5`, `−3,2`, `42,1 %`, `1 234,50 €`, dates `09/2026`, `08.09.`,
times `14.30`. Only EUR is shown as currency; other units are bare numbers
with the unit on the axis label. Nothing is converted.

Display rounding is half-up on the value as written (`18.25` -> `18,3 %`,
`0.125` -> `0,13 €`); recorded values (template metadata, datasets) stay
exact. A non-zero percent that would round to zero is shown as a bound,
`< 0,1 %` (`> −0,1 %` below zero), never `0,0 %`; likewise a percent that
would round to 100 but is not exactly 100 is `> 99,9 %` (or `< 100,1 %`
above it, since rates over 100 % occur; mirrored for −100), never
`100,0 %`. The bound's step follows the display decimals. When the locale groups
digits, the x axis takes the largest tick count whose narrowest gap between
labels is at least 1.5 times the group separator's width, so `10 000` and
`12 500` cannot run together; positions stay matplotlib's 1-2-2.5-5-10
steps, and a theme without a locale (`default`) keeps matplotlib's locator.

A theme is immutable: construction copies lists into tuples and mappings
(`semantic`, `targets`, `provenance`, recursively) into read-only dicts, and
`register` stores such a copy, so `name@version` always resolves to what was
checked. Malformed theme JSON (e.g. `semantic` as a list) is a `ThemeError`
with structured issues.

## Accessibility checks

`check_theme(theme)` returns `ValidationIssue`s; `register()` refuses a
theme with any error (`ThemeError.issues`), keeps warnings (see
`ThemeRegistry.findings`). Colours drawn with an opacity are blended over
the background first, i.e. checked as seen.

| Code | Rule | Severity |
|---|---|---|
| `text_contrast` | foreground and muted vs background >= 4.5:1 (WCAG 2.x 1.4.3) | error |
| `mark_contrast` | palette, signal, semantic, reference (line and fill opacity) and forecast colours (each palette colour at the forecast fill opacity and at the forecast line opacity, default 0.75) vs background >= 3:1 (WCAG 2.x 1.4.11) | error |
| `palette_indistinct` | every palette pair CIEDE2000 >= 15 | error |
| `palette_cvd_indistinct` | every pair >= 8 after protanopia and deuteranopia simulation (Machado et al. 2009, severity 1) | error |
| `palette_tritan_close` | same under tritanopia | warning |
| `diverging_cvd_indistinct` | diverging end stops >= 8 under protan/deutan simulation | error |
| `colormap_near_background` | a colour-map end stop < 1.5:1 vs background (would look like a blank "no data" cell) | warning |
| `palette_unchecked` | no palette declared (renderer default cycle) | info |

Structural problems are `invalid_theme`, `forecast_not_distinguishable`,
`signal_in_palette`; registry errors `duplicate_theme`, `unknown_theme`,
`unknown_theme_version`, `unknown_target`. The WCAG thresholds are the
standard's; the CIEDE2000 thresholds are this project's choice for small
chart marks (a just-noticeable difference is about 2.3), not a standard. The
simulation is a model; it does not replace testing with people.

## Built-in themes

`default`, `minimal` and `presentation` are the adapter's former presets,
expressed as themes. The 28 gallery figures that existed before themes,
under each of the three presets, render to the same SVG and PNG bytes as
before (84 renders compared). matplotlib's default colour
cycle is not checked (`palette_unchecked`): tab10's orange and green differ
by only 1.3 under simulated protanopia.

### `dashboard-dark` 1.0.0 and `report-light` 1.0.0

Neutral themes with no organisation's identity in them: `dashboard-dark` for
internal dashboards and slides, `report-light` for email and print. Series
colours come from Paul Tol's published colour-vision-safe qualitative
schemes ("vibrant", "muted", "bright", "light"; SRON/EPS/TN/09-002, 2021),
colour maps from matplotlib's `cividis`, greys are plain neutrals chosen
for contrast. Each value's origin is in the theme's `provenance`. The tests
(`tests/themes/test_builtin_themes.py`) assert the properties below, not the
hex values.

| Slot | `dashboard-dark` | `report-light` |
|---|---|---|
| background | `#16181d` | `#ffffff` |
| foreground / muted | `#e8e8e8` (14.5:1) / `#a3a3a3` (7.0:1) | `#1a1a1a` (17.4:1) / `#616161` (6.2:1) |
| grid | `#2b2e35` (1.3:1) | `#dcdcdc` (1.4:1) |
| palette | Tol cyan `#33bbee`, magenta `#ee3377`, teal `#009988`, pink `#ffaabb` | Tol blue `#0077bb`, green `#117733`, purple `#aa3377`, indigo `#332288` |
| signal (reserved, never a series colour) | Tol orange `#ee7733` (6.2:1) | dark amber `#a65e00` (5.0:1) |
| semantic | positive `#44bb99`, negative `#ee6677`, attention = signal | positive `#117733`, negative `#cc3311`, attention = signal |
| sequential | `cividis` on [0.3, 1] (low end visible on the ground) | `cividis` on [0, 0.8] (high end visible on white) |
| diverging | cyan / grey `#6b6e76` / magenta | blue / grey `#d0d0d0` / red `#cc3311` |

Both use fi-FI number and date formatting, email (1200 x 750 px) and slide
(1920 x 1080 px) targets, dashed and hatched forecasts at 75 % opacity, and
DejaVu Sans (shipped with matplotlib), so a render is byte-identical on every
machine. A theme may list other families; the first installed one is used.

Measured (`themes.describe(name)["accessibility"]`):

| | text | muted | palette vs ground | min pair ΔE00 | protan | deutan | tritan |
|---|---|---|---|---|---|---|---|
| dashboard-dark | 14.5 | 7.0 | 8.0, 4.6, 5.0, 9.9 | 22.9 | 13.7 | 16.5 | 13.6 |
| report-light | 17.4 | 6.2 | 4.8, 5.7, 6.1, 12.2 | 26.4 | 11.2 | 18.0 | 11.6 |

## Organisation themes

This library ships no organisation's brand. An organisation publishes its
themes from its own package and registers them at start-up with
`ieeVisualizer.themes.register(theme)` (or `default_registry.register`, or a
`ThemeRegistry` of its own). Registration runs the same structural and
accessibility checks as the built-ins and refuses a second, different theme
under an existing `name@version`. Register before rendering: a spec naming a
theme that is not registered renders with `default` (see below).

## Breaking changes

* **2026-10-15, theme renames, no aliases.** The two organisation-named
  built-in themes `<organisation>-admin-dark` and
  `<organisation>-report-light` became `dashboard-dark` and `report-light`,
  and both were redesigned as neutral themes (new palettes; the old values now
  live with the organisation that owns them). The library is pre-1.0 and
  unpublished, so the old names were removed outright rather than kept as
  deprecated aliases.
* **What an old name does now.** A spec or request naming a theme that is
  not registered (including the removed names) **does not fail**: it renders
  with `default` and the result carries an `unknown_theme` warning (with a
  "did you mean" suggestion when a registered name is close). Callers that
  must not fall back should treat an `unknown_theme` warning on
  `RenderResult.warnings` as an error, or resolve the theme first with
  `themes.get(name)`, which raises `ThemeError` (`unknown_theme`).

## Renderer additions used by the business templates

Bar style keys (renderer-neutral hints, matplotlib honours them):
`value_labels: true` prints each bar's value at its end in the theme's
locale. When the figure is drawn (at the size and dpi actually drawn) the
labels are arranged so that no two overlap and each stays within its bar's
cell (halfway to the neighbouring bars): on vertical bars, labels wider than
their cell are all turned 90 degrees, reading upwards from the bar end;
labels still too thick are shrunk to one common size down to 6 pt, and if
even that does not fit they are left out with a `value_labels_omitted`
warning rather than drawn over each other (the warning says whether the bars
are too narrow or bars of a `bars_overlap` warning cover each other). The
warnings describe the layout actually saved: each draw withdraws the
previous draw's. Then the value axis is widened
just enough that every label lies inside the plot; an end fixed by
`axes.<ch>.limits` is kept and a label that does not fit is a
`value_label_outside_axes` warning (`ieeVisualizer/render/mpl/value_labels.py`).
Value labels are drawn on 2D cartesian bars only; in `polar` the key gives a
`style_key_ignored` warning.
Bar layers sharing categories are dodged side by side by the renderer
(RENDERING.md, "Bars"); `bar_width` (0-1) and `bar_offset` (-0.5-0.5) are
explicit overrides that place one layer's bars as fractions of the category
slot (the default group fills 0.8 of it) and take that layer out of the
dodging. No built-in template sets them. A
template that writes numbers into text records `label_locale` in its
metadata; rendering with a theme that formats numbers differently adds a
`label_locale_mismatch` warning.

## Known gaps

- 3D panes, colorbar outlines and the hatch colour of forecast bars keep
  matplotlib's defaults (the hatch follows the bar's edge colour).
- Fonts other than DejaVu Sans are used only if installed; nothing reports
  a fallback.
- Number-format checks cover the matplotlib adapter only.
