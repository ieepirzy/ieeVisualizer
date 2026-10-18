# Phones, tablets and email

**Status:** design note and first slice, 2026-10-15. Reporting email is read
on phones first. Email clients run no JavaScript, so an email carries PNG
figures plus HTML (text, KPI tiles, tables). Everything here is about being
*legible and honest at that size*; disability accessibility (screen readers,
colour-vision deficiency) is out of scope for this note and not extended.

House rule, applied to small screens: a figure on screen is a claim. Nothing
is shrunk below the minimum, dropped or cut to make it fit without saying so
**in the output the reader sees**, or at least as a structured warning on
the render result that a pipeline can refuse to send.

## Targets

| Profile | Content width (CSS px) | Image width | Pixel density carried | Narrow rules |
|---|---|---|---|---|
| `email-phone` | 320 (narrowest it is shown at) | 1200 px | 3.75x at 320 px, 2.08x at 576 px | yes |
| `email-tablet` | 600 | 1200 px | 2x | no |
| `desktop` | 960 | 1920 px | 2x | no |

Phones are 360-430 CSS px wide at 2x-3x device pixel ratio; tablets 768-1024
CSS px at 2x. An email is a single column of at most 600 px (here 576 px of
content between 12 px gutters). On a 360 px phone the column is 360 - 2 x 12
= 336 px, less any margin the client adds; `email-phone` assumes at least
**320 px**, so up to 16 px of client margin is covered. `render_email` is
stricter: it takes 320 px as the narrowest *screen* and lays the email out
for the 296 px inside its gutters (figures included), so 320 px phones are
covered too.

**How clients scale images.** An image written as
`<img width="W" style="width:100%;max-width:Wpx;height:auto">` is drawn at
the column width up to W px; Outlook for Windows ignores `max-width` outside
tables and uses the `width` attribute [4]. So the same PNG is shown about
320 px wide on a phone and 576 px on a tablet: its text scales with it. The
phone is the binding case, so **email images are laid out for the phone**
(`email-phone`) and rendered 1200 px wide, which is sharp at 3x on phones
and at 2x in a tablet's 576 px column. One image serves both.

**Why not `srcset`/`<picture>`.** caniemail's tests show `srcset` and
`<picture>` unsupported in Gmail (all platforms), Outlook (Windows,
Outlook.com, iOS, Android) and newer Outlook for Mac; Apple Mail supports
them [1][2]. A phone-first image works everywhere, so no art direction by
markup.

**Why no media queries.** `@media` is unsupported in Outlook for Windows and
Gmail's mobile web client, and in the Gmail apps for non-Google accounts
[3]; Gmail otherwise supports width queries in a `<style>` block [5]. The
email layout therefore must reflow without them: fluid `inline-block` tiles
at most half the column wide sit two across on a tablet and one per row on a
phone; a client without `inline-block` stacks them, which is also legible.

**Dark mode.** Clients differ: Apple Mail leaves an email alone unless it
opts in; Outlook.com, the Outlook apps and Gmail for Android darken light
backgrounds ("partial invert"); Gmail for iOS and Outlook for Windows invert
everything; `prefers-color-scheme` is honoured by Apple Mail only [6], and
`color-scheme` is not supported by Gmail or Outlook [7]. Images are not
recoloured. So: figures are opaque PNGs with their own background (never
transparent, which would put dark text on an inverted dark ground); the HTML
sets explicit colours and declares `color-scheme: light`; a light figure on
an inverted page shows as a light card, which stays legible.

**Message size.** Gmail clips a message whose HTML exceeds about 102 KB
behind "View entire message" [8]: the rest of a report would silently not be
shown, so `render_email` warns (`email_clipped_by_gmail`). Images are
attachments (`cid:`), not `data:` URIs, which Gmail does not display.

## Minimum text size: 12 CSS px in images, 13 CSS px in HTML

Platform floors: Apple's HIG gives **11 pt** as the minimum text size on
iOS/iPadOS (17 pt default) [9]; Material 3's smallest styles are Label Small
**11 sp** and Body Small **12 sp** [10] (iOS pt and Android sp are CSS px at
1x). Figures are read at arm's length, often in passing, and an image cannot
be zoomed as text reflows, so this project takes **12 CSS px** for every
text in a figure (tick labels, legend, captions, value labels, "no data"
notes) - one step above both platforms' absolute minimum. HTML text is at
least **13 px**: iOS Mail enlarges smaller text to 13 px on its own [11],
which breaks a layout measured for the smaller size (the email also sets
`-webkit-text-size-adjust:100%`). Body text is 15 px.

**Mapping to Matplotlib.** A figure W inches wide shown at D CSS px makes
s pt text s/72 x D/W CSS px tall (em size). `RenderProfile.figure_request()`
sets `dpi = 72 x pixel_width / css_width`, so **1 pt = 1 CSS px** at the
display width and the minimum is 12 pt; in general
`min_pt = 12 x 72 x W / D` (`RenderProfile.min_text_pt`). A view's figures
use the same formula with their slot width as D.

"Text size" is the font's em size; the audit also has each line's box from
the renderer's own metrics (`get_text_width_height_descent`), which the
tests convert to device pixels.

## Profiles in the API

```python
from ieeVisualizer.render import get_profile, render, check_legible
from ieeVisualizer.view import render_view, render_email

req = get_profile("email-phone").figure_request("png")   # 1200 x 960 px, 270 dpi
result = render(spec, req)            # RenderRequest(profile="email-phone", ...)
check_legible(result)                 # raises LegibilityError on legibility_* warnings
render_view(view, "png", profile="email-phone")          # 320 CSS px, scale 3.75
email = render_email(view, "email-phone")                # .html, .images, .warnings
```

`RenderRequest(profile=..., display_width=...)`: `display_width` is the CSS
width a figure is shown at when narrower than the column (a tile, a card).
Both are recorded in the request (and so the artifact id) only when set.
Renders without a profile keep their bytes except where a rule below applies
to every render: node-label attribution in networks (the `org_chart` example
changes) and the CJK font fallback (text the theme's font lacks).

## What a profile render does

**Every profile** (matplotlib adapter):

- every rcParams font size and every size the adapter sets itself
  ("no data", value labels, annotations, caption) is raised to the minimum;
  value labels shrink no further than it (they are left out with the
  existing `value_labels_omitted` warning instead);
- the title and axis labels are wrapped to the figure, a title centred over
  the axes is re-wrapped if it still runs off the image (again after every
  layout pass, since value labels, legends and constrained layout move the
  axes it is centred over); a title with a word wider than the room either
  side of the axes' centre moves sideways, only as far as needed, to lie
  inside the image, and text
  annotations that would run past the right edge wrap at their place, but
  only into a column at least 8 em wide (a narrower one would stack the
  words and squeeze the plot; the text is then left whole and reported as
  clipped);
- tick labels that touch: numeric and date axes get fewer ticks; a category
  x axis turns its labels upright. Category labels are never thinned for a
  profile (the >50-category thinning of other renders is off); if they still
  touch, `too_many_categories_for_profile` says how many and what to do;
- the **audit** records every line of text drawn into the saved PNG and adds
  `legibility_text_too_small`, `legibility_text_overlap` and
  `legibility_text_clipped`; a plot squeezed below 25 % of the image either
  way is `legibility_plot_collapsed`, matplotlib giving up on the layout is
  `layout_collapsed`, and a legend not wholly inside the image is
  `legend_not_visible`;
- every render (profile or not) reports a text annotation anchored outside
  the axes' limits, which matplotlib silently does not draw
  (`annotation_outside_view`), and a title that runs past the figure's edge
  and is cut off (`title_clipped`).

**Narrow profiles** (`email-phone`) also: an `"auto"` legend that leaves the
axes goes below them, never to the right; a date axis label is left-aligned
clear of the date offset (`2026-Oct`); in views, grids and horizontal stacks
drop columns until each slot is at least 150 px (KPI tiles stack), figures
are at least the profile's aspect (0.8) tall, KPI values shrink no further
than 12 px before switching to the existing compact notation (reported as
`kpi_value_compacted`, with the exact value), and a
left-to-right flow that would be scaled below the minimum is laid out top to
bottom (the layout is only a hint); if still too small,
`flow_text_too_small`.

**Taller, never wider.** A profile render whose content does not fit at
the minimum text size is drawn again taller (up to 4 times its width; the
width never changes), with a non-gating `figure_grown` note saying how tall
and why: rows of horizontal bars get room for their value labels, y category
labels stop touching, a legend below the plot gets its height, a plot
squeezed below 25 % gets 35 %. Only the final layout counts for
`layout_collapsed`: constrained layout giving up during a measuring pass (a
legend still inside the axes, value labels not yet fitted) is not reported,
unless the saved plot is the collapsed one (`legibility_plot_collapsed`).
Wrapping never separates a number from a unit-only word after it (`20 %`,
`5 €`). Value labels of a profile render are left out
of constrained layout (the value axis is widened to hold them instead), and
every draw widens from the spec's limits, so a measuring pass at another
layout cannot leave the bars squeezed. A legend below the plot on a narrow
profile wraps long names at spaces until one column fits under the plot,
then takes as many columns as fit. Views and emails lay a figure out at the
height its render will take (`FigureRenderer.fitted_height`).

**Row notes** (a funnel's rate column): a template records the text
annotations whose x is only a layout position, not data, by content
(`metadata["template"]["row_notes"]`: text, x and row of each); at render
time an annotation is a row note only if its text, x and row are exactly a
recorded note's, so a caller's own annotation, wherever it sits in the
list, is never moved. A recorded note that matches no annotation (the
spec was edited after the template built it) is reported (`row_note_stale`,
a note); its edited form stays at its own data position and is refused
(`row_note_detached`) if its text runs past the plot's right edge, away from
its row's bar. A note drawn twice is `row_note_duplicated` (gating, naming
the annotations). An annotation anchored on a category row that wrapping
makes taller keeps its own placement (just above its anchor) when that stays
in its row; only if it would climb into the row above is it centred on its
anchor, or else hung below it - each only if it stays in its row and clear
of the row's value labels (decided after every layout). One whose drawn text
still reaches into another row's band is `annotation_outside_row` (gating). When one lies over a value label or
past the plot's right edge, every row note moves under its own row's value
label, wrapped to the room left, and the plot grows until each row holds
both. Words and rows stay. Any other annotation keeps its data position; if
it collides, the audit reports the overlap or clipping and the gate refuses.

**Networks** (node-link layouts; the adjacency matrix view is not yet
adapted): labels, legend and caption at least the minimum, the title 3 pt
larger. On a phone the graph takes the full width with the legend sections
below it, side by side where they fit; elsewhere the legend keeps its column
on the right. The layout leaves room at its edges for half a label across and
two lines up and down. A label tries its full text, then wrapped at spaces
(2, 3, ... lines), at the layout's own places, then at more places (above,
beside, below kept inside the image, clear of the legend and title), before
the old shortening; labels that found no clear place are placed first in a
second try, and the figure grows while labels do not fit (not for top-down
layered layouts, which are crowded across). A network still too big is
refused with `labels_skipped`, `labels_abbreviated` or `label_ambiguous`,
whose message says why it stopped (height limit, top-down layout, rounds)
and what to do (wider profile, fewer nodes, the text outline).

**A node label belongs to its node** (every network render, profile or
not): a place is taken only if the gap between the label box and its own
node's rim is at least 3 pt smaller than the gap to every other node's rim
(rim to rim, so a large node is judged like a small one). A label with free
places but no such one is drawn whole at the first free place with a gating
`label_ambiguous` warning naming the node; a whole ambiguous label is
preferred to a shortened one. Without a profile, when looking for clear
places would shorten or drop more labels than main's rule (the first free
place), main's placement is kept and its unclear labels are flagged.
**Renders without a profile change** where main's first free place for a
label was not clearly its node's: the `org_chart` example (three labels move
below their nodes) and 4 of 60 seeded random networks in the review sweep;
the `network` and `dependency_map` examples keep main's bytes (recorded in
`tests/render/data/main_render_hashes.json`).

**Org charts are effectively not sendable on a phone.** A top-down tree is
crowded across and the width is fixed. In sweeps of 40 random org charts with
10-25 nodes (bushy, shallow trees: each node's parent drawn from the
first third of the nodes; names from 20 unit names; `random.Random(seed)`
for seeds 1, 2 and 3), the gate refuses 39-40 of 40 at `email-phone`, 31-36 at
`email-tablet` and 15-19 at `desktop` (the template's own 9-node example
passes everywhere). In a phone email, send
the chart as a list or table instead - the network's text outline
(`network_outline`) or a `Table` of unit, parent and headcount - and keep the
figure for tablet or desktop renders. A phone layout for trees is issue #30.

**Flows** on a profile: after the top-to-bottom fallback, node boxes narrow
step by step (labels wrap at spaces onto more lines, never narrower than the
widest word, box padding 20 instead of 28 px) until the flow is drawn at the
minimum, or `flow_text_too_small` if not. Views on narrow profiles pad 12 px
instead of 24 px, like the email's own gutters.

**Wide tables.** A view PNG with a profile never cuts a cell: a table whose
columns do not fit is drawn as one card per row (each cell under its column
name; text values wrapped between words, numbers, dates and booleans whole -
a value or word wider than its card is `value_too_wide_for_screen`, never
split), with the same "One card per
row" note as the email. Without a profile the PNG cuts cells with "…" as
before. The email shows a
table that does not fit the phone width as one card per row with every cell
whole, and writes "One card per row: the N columns do not fit side by side
in the N px this table gets on a phone screen" above it (the slot, not the
screen width). There is no horizontal scrolling.

**Font fallback.** The first installed CJK font of Noto Sans CJK (JP, SC,
TC, KR), Source Han Sans, WenQuanYi Zen Hei / Micro Hei or Droid Sans
Fallback is appended to every render's font family list (figures, networks
and view PNG labels), so a character DejaVu Sans lacks is drawn from it
instead of as a box. This applies to every render, with or without a
profile: text the theme's font draws (Latin, the recorded cases) keeps
main's bytes, but text with characters it lacks (CJK) now draws them instead
of boxes, so those renders change. The glyph check resolves the same list,
so a character no installed font has is still `glyph_missing`. Matplotlib's
"Failed to find font weight ... for <the fallback>" record (such fonts often
have one weight) is dropped on its logger while rendering, on the rendering
thread only; the same record for a theme's own font still reaches the log.

## The send gate

`check_legible(result)` (a `RenderResult` or an `EmailRender`) raises
`LegibilityError` for any warning in `GATING_CODES`, and, for a result made
for an email profile, in `EMAIL_GATING_CODES`: `axis_untitled`, an axis
showing several quantities with no title, unit or legend saying which (its
numbers mean nothing to a reader; a plain render keeps it as a warning).
`GATING_CODES` are the legibility codes
above, and every code meaning the output shows less than the data holds or
could not be laid out - `value_labels_omitted`, `value_label_outside_axes`,
`no_data_unplaceable`, `category_labels_thinned`, `table_cells_truncated`,
`flow_text_too_small`, `kpi_value_compacted`, `labels_abbreviated` /
`labels_skipped` / `label_ambiguous` / `matrix_labels_omitted` (networks),
`annotation_outside_view`, `annotation_skipped`, `layout_collapsed`,
`email_clipped_by_gmail`, `email_not_phone_safe`,
`value_too_wide_for_screen` (a value that must stay whole is wider than the
room it gets on the phone, so it would run off the screen), and
`glyph_missing`: a character drawn as an empty box. It is looked up, not
listened for: after layout, every visible text artist's own FontProperties
(family list, style, weight) is resolved to font files the way Matplotlib's
Agg text path does (`fontManager._find_fonts_by_props`, first to last
fallback) and each character is looked up in those fonts (`get_char_index`).
Only " " and "\n" are skipped: tabs, carriage returns (a stray CSV "\r"),
other controls and other spaces are looked up like any character, because Agg
draws them as boxes when the font lacks them. With text shaping (matplotlib
3.11+, HarfBuzz) a lacking default-ignorable (U+200B, U+200D, U+FE0F, ...;
not the Hangul fillers U+115F, U+1160, U+3164, U+FFA0, nor U+180F or
U+1BCA0-1BCA3, which HarfBuzz still draws as boxes) draws nothing and a lacking fallback space (U+00A0, U+2000-200A, U+202F,
U+205F, U+3000) draws a space, so those are not reported there; before 3.11
they are boxes and are. Mathtext is parsed by Matplotlib's mathtext parser:
more dummy glyphs (¤) than the text itself holds means a character is
missing. The parser's own "does not have a glyph" log records are filtered
on that one logger, for the checking thread only, during the parse.
Matplotlib's shared mathtext parser is not thread-safe, so the checker's
parses are serialised behind a lock and retried once; if a parse still fails
after the draw succeeded (another thread's draw racing it), the text is
reported unchecked as gating `glyph_check_failed` - the check fails closed. View PNG labels and email flows are
checked with the exact font the PNG draws them in. The answer depends only on
the text and its font, so the host's logging or warnings configuration
cannot change it; concurrent renders cannot make it pass a text it should
refuse (at worst they turn a mathtext answer into `glyph_check_failed`). `tests/render/test_glyph_truth.py`
checks it against pixels: each character drawn alone is compared with how the
running matplotlib draws a missing glyph (LastResort box on 3.11+, .notdef
before), for several fonts and the three mathtext font sets. CI runs it on
whatever matplotlib the 3.9 and 3.12 jobs install (the latest release for
each Python: 3.9.4 and 3.11.x today) and on the 3.6.3 floor job. SVG and HTML text is
left to the reader's fonts and not checked. A pipeline that must not
send an illegible or incomplete report calls it on every output. The tests
check the property it protects: for every template at every profile, each
word a desktop render draws outside the tick labels is drawn outside the
tick labels of the profile render too, or that render fails the gate.

## Email HTML

`render_email(view, profile="email-phone", image_src=...)` returns the HTML,
the PNGs it references (`figure-<n>.png`, `flow-<n>.png`, `src="cid:<name>"`
by default) and warnings. Inline styles only; no `<style>`, script, SVG,
class, link or media query; `role="presentation"` tables for the column;
KPI tiles and table cards as fluid `inline-block` boxes; each flow followed
by its text version (shown, since `<details>` is not dependable in mail).
`EmailRender.text` carries the same content as plain text; assembling the
message is `build_message` (next section).

- Every text container has `overflow-wrap:anywhere; word-break:break-word`,
  so a URL or id longer than the screen wraps instead of widening the email.
  Table values never wrap (`white-space:nowrap`): a number or date split over
  two lines reads as two values. One measure (`whole_width`: DejaVu width
  plus 6 %) decides every keep-whole case against the room the value really
  gets at its depth - the screen width minus the two 12 px gutters (296 px on
  a 320 px screen), minus every enclosing
  card (26 px), table card (30 px) or KPI tile (30 px): a table only when
  every column fits its widest value and longest header word, otherwise one
  card per row, where only text values may wrap; a number, date or KPI line
  that does not fit even its card is `value_too_wide_for_screen`, never
  split and never silently over the edge. The 6 % comes from measurement in
  Chromium at 13 px, normal and bold, on dates, grouped numbers, long words,
  WWWWWW/MMMMMM and CJK: DejaVu Sans renders at 1.000 of the model; Liberation
  Sans (0.80-0.97), FreeSans (0.79-0.97) and WenQuanYi Zen Hei (0.74-0.95) are
  narrower; Inter, the widest substitute seen, reaches 1.047 (MMMMMM).
  Against the reviewers' fuzz (300 random emails x 5 fonts x 320/360/430 px)
  nothing that overflows or splits passes the gate; 47 of the 171 refusals
  would have fitted every tested font: 14 of those are compacted KPI numbers
  (refused by design, the shown number differs) and 27 are values within the
  6 % margin. The 360 realistic emails all pass.
- Image names are Content-IDs, `figure-<n>.<12 hex of the PNG's SHA-256>.png@ieevisualizer`:
  the same picture keeps its name, other data gives another. A figure's
  `alt` summarises its measured values (references resolved first) for
  clients that block images: per layer the count and range of the value
  channel - y (x for horizontal bars), a histogram's samples, a heatmap's
  colour, a quiver's vector magnitude (with w in 3D); in 3D only a colour
  channel, since x, y and z are positions - never positions.
- Lines break at spaces outside quoted attributes so none exceeds 900
  UTF-8 bytes (SMTP's limit is 998 bytes); `lang` must be a language tag.
- A slot narrower than 120 px on the phone (deep nesting) is refused with a
  `too_narrow` issue and its pointer; a flow image larger than the view PNG
  limits (`MAX_PNG_SIDE_PX`, `MAX_PNG_PIXELS`) is refused (`view_too_large`)
  before anything is rasterised.
- `email_dark_figure` when a figure's theme has a dark background (the email
  declares a light scheme); `email_not_phone_safe` on an `email-tablet`
  render.

**Browser layout tests** (`tests/view/test_email_browser.py`) load the email
in headless Chromium (Playwright) at 320, 360, 430 and 768 px and assert: no
horizontal overflow, no two text boxes overlapping, no computed font below
13 px, images inside the screen, KPI tiles one per row up to 430 px and two
across at 768 px, and every table value on one line (weekly briefing, a
revenue table with 23 456 789.5-style values, a short table with long
headers); also for a view with 140-character URLs in every text field, and
a seeded fuzz (random tables of int/float/str/datetime columns in 0-6 nested
cards, some in grids) where each email must fit 320 and 360 px with no split
value or else be refused by the gate. CI installs Chromium and sets `IEEV_REQUIRE_BROWSER=1`, so they run
there; locally they skip with the reason when no browser is available.

**Renders without a profile** are compared with SHA-256 hashes recorded from
a `git archive` of main per matplotlib version
(`tests/render/data/record_main_hashes.py`, which says how to re-record). CI
sets `IEEV_REQUIRE_MAIN_HASHES=1`, so an unrecorded matplotlib version fails
there instead of skipping. That is by design: a new matplotlib release
changes the bytes, and the test fails until its hashes are recorded. To
re-record, export main (`git archive origin/main | tar -x -C /tmp/main`) and
run `PYTHONPATH=/tmp/main python tests/render/data/record_main_hashes.py`
with the new matplotlib; commit the updated `main_render_hashes.json`.

## Sending a briefing

```python
from ieeVisualizer.view import build_message, render_email, write_eml

email = render_email(view, "email-phone")
msg = build_message(email, subject="Weekly briefing: week 41",
                    sender="Reporting <reports@example.com>",
                    recipients=["Management <management@example.com>"])
write_eml(msg, "briefing.eml")        # preview; smtplib.SMTP(...).send_message(msg) sends
```

`build_message` returns a stdlib `EmailMessage`: `multipart/alternative`
holding a `text/plain` part and a `multipart/related` part with the HTML and
one `image/png` per image (`Content-ID: <figure-0.<hash>.png@ieevisualizer>`,
`Content-Disposition: inline`, a filename). Every `cid:` the HTML references
has exactly one part and every image is referenced; otherwise it raises
`MessageError` `cid_mismatch` (an email rendered with a custom `image_src`
cannot be sent this way). Text parts are UTF-8 quoted-printable and images
base64, with CRLF line ends and no line over 76 characters (RFC 2045); a
body line starting with "From " is written `=46rom` so mbox tools cannot
mangle it. The related part carries `type="text/html"` (RFC 2387).

**Plain text.** Clients that block images or show no HTML get the numbers
anyway: `EmailRender.text` has the title, every text and note, each KPI's
value with its unit, its comparison line ("▲ +23 orders vs week 40 (389
orders)") and source, exactly as written (never the compacted tile form),
every table cell as a `header (unit): value` line, each figure's `alt`
summary (the two bounds of a range are named "lower bound" / "upper bound",
and an interval's under the statement its legend gives, e.g. "80%
prediction interval (forecast), lower bound: ...";
the observed point a forecast line starts from is not counted as a forecast
value) and each flow's text version. A percentage KPI's delta is in
percentage points (`▲ +1.5 pp`), in the tile and in the text alike.

**Gate.** `build_message` calls `check_legible` first and raises
`EmailGateError` (a `LegibilityError`; `.codes` lists the gating codes, and
`.issues` has their messages and pointers) for any render the gate refuses,
including every `email-tablet` render (`email_not_phone_safe`). The verdict
is the one `render_email` reached: it records digests of the HTML, text,
images, profile and gating codes (`EmailRender.seal`), and `build_message`
refuses a render changed since - warnings cleared, HTML edited, an image
swapped - with `MessageError` `render_modified` and the field's pointer, an
`EmailRender` built by hand (`not_rendered`) and a non-phone profile
(`not_phone_safe`). `allow_ungated=True` builds any of these anyway; that
is the caller's explicit choice. `text_fallback=` (which replaces the
generated text and could say anything) needs its own
`allow_custom_text=True` (`custom_text_not_allowed` otherwise); the gate,
seal and profile checks still apply to it.

**Headers.** Subject, sender, recipients, `date` and `message_id` are
checked before anything is built: a CR, LF, other control character or
U+2028/2029 is `header_injection`; an address must be exactly one
`Name <local@domain>` or `local@domain` (ASCII, dotted domain; one per
list entry, so `"a@x.example, b@y.example"` in one string is refused, not
split); an empty subject or recipient list is refused. A subject or display
name holding encoded-word syntax (`=?utf-8?q?...?=`) is refused
(`encoded_word`): the stdlib decodes it while folding the header, so
`"=?utf-8?q?CEO_=3Cceo=40x.example=3E?= <reports@example.com>"` would be
written as `CEO <ceo@x.example> <reports@example.com>`, an address of the
caller's string's choosing. So are invisible format characters (bidi
overrides, zero-width; `format_character`), display names that look like
an address once NFKC-normalised (`＠`, `﹫`, `＜` included;
`address_in_display_name`) and names that show blank (whitespace, Hangul
fillers; `blank_display_name`). A local part must be ASCII, at most 64
characters and free of `=?`; a domain at most 253 characters with labels of
at most 63 (RFC 5321); an internationalised domain is written in IDNA
(`pääkaupunki.fi` as `xn--...`). `date` must be an RFC 5322 date with
a time zone (`invalid_date`; default: now, GMT) and `message_id` an
`<id@domain>` (`invalid_message_id`). Each issue points at its argument
(`/subject`, `/recipients/1`).

Subject, From and To are encoded and folded by `ieeVisualizer.view._headers`,
not by the stdlib policy: on Python 3.9 the policy folds a long non-ASCII
header with the space between two words *between* encoded words, where RFC
2047 tells decoders to drop it ("kasvoi Pääkaupunkiseutu" read
"kasvoiPääkaupunkiseutu" in 50 of 400 random Finnish subjects). Here an
ASCII word is written as itself (an atom, or a quoted string in a display
name) and each run of consecutive non-ASCII words becomes as few base64
encoded words as fit, with the spaces inside the run inside the encoded
text; the whitespace between an encoded word and plain text is kept by
every decoder. A run is split only where it exceeds the 75-character limit
of one encoded word (after a word where possible), and lines are folded
only between tokens, at most 76 characters. Only ASCII spaces separate
words; a no-break or thin space is encoded with its word.

Correct (RFC 2047 6.2) decoders read every header back exactly: Node's
`mailparser` over 400 Finnish subjects and names and a 4 000-case random
header fuzz, from messages built on Python 3.9, 3.12 and 3.13, and Python
3.13's parser; the tests use a strict RFC 2047 decoder as their oracle on
every Python. Python's default-policy parser before 3.13 keeps the
whitespace between two adjacent encoded words, against 6.2: there a split
run shows one extra space (103 of 800 long Finnish headers on 3.12), never a
missing one, which the tests assert. Gmail, Outlook and Apple Mail are
expected to decode per the RFC; this has not been checked in those clients
(follow-up 7: real-client screenshots).

**Previewing.** `write_eml` writes the message as sent; open the `.eml`
in Thunderbird, Apple Mail or Outlook (double-click, or drag it into the
client) to see it as a recipient would, images included. Python's own
parser reads it back (`email.message_from_bytes(..., policy=policy.default)`).
`examples/weekly_briefing.py` builds a whole briefing for a fictional
company, prints the gate verdict and writes `briefing.eml`, plus
`briefing.html` and its PNGs for a browser (narrow the window to 360 px).

**Known client caveats** (from the sections above; not yet confirmed in
real-client screenshots, follow-up 7): Outlook for Windows ignores
`max-width` on images and lays tiles out without `inline-block`
(they stack); Gmail with non-Google accounts and Outlook for Windows ignore
media queries (none are used); dark-mode clients may invert the page but not
the images, which stay light cards; Gmail clips HTML over about 102 KB (the
gate refuses that, `email_clipped_by_gmail`); images may be blocked until
the reader allows them, so the `alt` text and the plain-text part carry the
values.

## Measured in this slice

`examples/mobile_gallery.py` renders every template and gallery view at both
email profiles and writes, per file, every warning the gate refuses
(`legibility.json`). Every template example (bar comparison, funnel,
timeseries, vector field, dependency map, network, org chart), both gallery
views as phone and tablet PNG and both emails pass the gate.
`tests/render/test_mobile_gate.py` checks, for every template example at
every profile (desktop included), that the gate passes and that every text
of the desktop render is in the profile render with the same words in the
same order and the same anchor, measured from the drawing
(`tests/render/anchored.py`): the node a reader model attributes a label to
(nearest box point to the edge of each drawn marker, read from the drawing,
independent of the renderer's rule), the bar a value label is drawn on, the
swatch drawn beside a legend text (colour, dash pattern, marker, hatch;
paired by position, scatter swatches by their marker offsets, not list
order), the category tick, an annotation's data anchor
if its text is drawn there and the category rows its text reaches into (or
those rows for a row note) - and nothing else; a test shows that swapping two
value labels, two legend swatches, the dashes of two same-colour entries of a
wrapped colour cycle, or shifting a note through its offset is told apart;
for both gallery views that the gate passes and no word of the desktop
render is missing; and, for the templates and 40 seeded random networks
(half with node sizes; default request, 1920 px without a profile and every
profile), that every node label is clearly its own node's
by that reader model or reported `label_ambiguous`.

## Follow-ups

These are layout improvements; the gate already refuses each case.

1. Network adjacency-matrix view: font floors and a phone layout.
2. Vertical bars too narrow for their value labels on a phone are refused
   (`value_labels_omitted`); a taller figure does not widen them. The
   `bar_comparison` template's `auto` orientation could also consider the
   number of bars per category.
3. Annotations that cannot wrap at the right edge: move them left of their
   anchor instead of reporting them clipped.
4. Ghost tables (`<!--[if mso]>`) so Outlook for Windows shows tiles side by
   side on desktop; real-client screenshots (Litmus /
   Email on Acid) before relying on the email layout in production.
5. A text summary visible under each figure (not only `alt`), for clients
   that block images and do not show `alt`.
6. Vector (SVG/PDF) audits draw the figure once more instead of measuring
   the saved draw; an exact audit needs a hook in those backends.
7. The view validator's narrow-slot check still assumes 24 px view padding
   on phones (12 px is used), so it is slightly stricter than the layout.

## Sources (accessed 2026-10-15)

1. caniemail, "srcset and sizes attributes" (data file `_features/html-srcset.md`, tested 2019-05, Outlook.com 2024-01): <https://www.caniemail.com/features/html-srcset/>
2. caniemail, "`<picture>` element" (tested 2024-04): <https://www.caniemail.com/features/html-picture/>
3. caniemail, "@media" (tested 2023-12; notes 6 "not supported with non Google accounts", 7 no height queries): <https://www.caniemail.com/features/css-at-media/>
4. caniemail, "max-width" (Outlook Windows: only on `<table>` / not on `<table>` by version): <https://www.caniemail.com/features/css-max-width/>
5. Google, "CSS support" for Gmail: <https://developers.google.com/workspace/gmail/design/css>
6. Litmus, "The ultimate guide to dark mode for email marketers" (chart Feb 2025): <https://www.litmus.com/blog/the-ultimate-guide-to-dark-mode-for-email-marketers>
7. caniemail, "color-scheme" (tested 2023-09): <https://www.caniemail.com/features/css-color-scheme/>
8. Gmail clipping at ~102 KB (vendor documentation; Google publishes no figure): <https://mxtoolbox.com/dmarc/email/email-clipping>, <https://help.activecampaign.com/hc/en-us/articles/115001060524>
9. Apple Human Interface Guidelines, Typography, "Default and minimum text sizes": <https://developer.apple.com/design/human-interface-guidelines/typography>
10. Material 3 type scale tokens (`TypeScaleTokens.kt`: BodySmallSize 12.sp, LabelSmallSize 11.sp): <https://android.googlesource.com/platform/frameworks/support/+/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/tokens/TypeScaleTokens.kt>
11. Campaign Monitor, iOS Mail CSS support ("minimum font size 13px"): <https://www.campaignmonitor.com/css/email-client/ios-11-mail>
