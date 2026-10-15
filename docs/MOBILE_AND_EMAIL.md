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
**320 px**, so up to 16 px of client margin is covered. Narrower viewports
(320 px phones) are below this target.

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
Both are recorded in the request (and so the artifact id) only when set:
renders without a profile are unchanged, byte for byte.

## What a profile render does

**Every profile** (matplotlib adapter):

- every rcParams font size and every size the adapter sets itself
  ("no data", value labels, annotations, caption) is raised to the minimum;
  value labels shrink no further than it (they are left out with the
  existing `value_labels_omitted` warning instead);
- the title and axis labels are wrapped to the figure, a title centred over
  the axes is re-wrapped if it still runs off the image, and text
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
  (`annotation_outside_view`).

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

**Wide tables.** The view PNG cuts cells that do not fit with "…" as before,
and with a profile says so (`table_cells_truncated`). The email shows a
table that does not fit the phone width as one card per row with every cell
whole, and writes "One card per row: the N columns do not fit side by side
on a 320 px wide screen" above it. There is no horizontal scrolling.

## The send gate

`check_legible(result)` (a `RenderResult` or an `EmailRender`) raises
`LegibilityError` for any warning in `GATING_CODES`: the legibility codes
above, and every code meaning the output shows less than the data holds or
could not be laid out - `value_labels_omitted`, `value_label_outside_axes`,
`no_data_unplaceable`, `category_labels_thinned`, `table_cells_truncated`,
`flow_text_too_small`, `kpi_value_compacted`, `labels_abbreviated` /
`labels_skipped` / `matrix_labels_omitted` (networks),
`annotation_outside_view`, `annotation_skipped`, `layout_collapsed`,
`email_clipped_by_gmail`, `email_not_phone_safe`, and `glyph_missing` (a
character the font has no glyph for, drawn as an empty box: figure text,
and in PNG views and email flows every label is checked against DejaVu
Sans). A pipeline that must not
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
Delivery (MIME assembly) stays out of scope.

- Every text container has `overflow-wrap:anywhere; word-break:break-word`,
  so a URL or id longer than the screen wraps instead of widening the email.
  Table values never wrap (`white-space:nowrap`): a number or date split over
  two lines reads as two values. A table is used only when every column fits
  its widest value whole and its longest header word, with 20 % to spare for
  a substitute font; otherwise one card per row, where only text values may
  wrap.
- Image names are Content-IDs, `figure-<n>.<12 hex of the PNG's SHA-256>.png@ieevisualizer`:
  the same picture keeps its name, other data gives another. A figure's
  `alt` summarises its measured values (references resolved first) for
  clients that block images: per layer the count and range of the value
  channel - y (x for horizontal bars), a histogram's samples, a heatmap's
  colour, a quiver's vector magnitude - never positions.
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
headers); also for a view with 140-character URLs in every text field. CI installs Chromium and sets `IEEV_REQUIRE_BROWSER=1`, so they run
there; locally they skip with the reason when no browser is available.

**Renders without a profile** are compared with SHA-256 hashes recorded from
a `git archive` of main per matplotlib version
(`tests/render/data/record_main_hashes.py`, which says how to re-record). CI
sets `IEEV_REQUIRE_MAIN_HASHES=1`, so an unrecorded matplotlib version fails
there instead of skipping.

## Measured in this slice

`examples/mobile_gallery.py` renders every template and gallery view at both
email profiles and writes, per file, every warning the gate refuses
(`legibility.json`). Pass the gate at both: timeseries, vector field, the
briefing email and both views as tablet PNG. Refused, with the reason
(follow-ups below): the three network templates at **every** profile,
desktop included (their own 7.5 pt labels), bar comparison on a phone (value labels left out),
funnel on a phone (rate column over the first value label), the network
templates (their own 7.5 pt labels), the pipeline flow on a phone (77 %
scale even top to bottom), the briefing table cut in the phone PNG.

## Follow-ups

These are layout improvements; the gate already refuses each case.

1. Network renderer: font floors for its explicit sizes and label placement;
   until then the gate refuses all three network templates at every profile.
2. Funnel template: put the stage-to-stage rate in the stage label or a
   measured column instead of a fixed data-coordinate position.
3. Profile-aware figure height: rows x minimum line height for horizontal
   bars (so value labels fit on phones), and room for a legend of many or
   long series names instead of `legend_not_visible`.
4. View PNG: card layout for tables that do not fit (the email already has
   it); 12 px instead of 24 px view padding on narrow profiles.
5. Flows: narrower node boxes (wrap labels at the slot width) on phones.
6. Annotations that cannot wrap at the right edge: move them left of their
   anchor instead of reporting them clipped.
7. Ghost tables (`<!--[if mso]>`) so Outlook for Windows shows tiles side by
   side on desktop; MIME assembly helper; real-client screenshots (Litmus /
   Email on Acid) before relying on the email layout in production.
8. A text summary visible under each figure (not only `alt`), for clients
   that block images and do not show `alt`.
9. Font fallback (DejaVu Sans plus an installed CJK font) so `glyph_missing`
   becomes rarer; the gate keeps refusing whatever is still missing.
10. Vector (SVG/PDF) audits draw the figure once more instead of measuring
   the saved draw; an exact audit needs a hook in those backends.

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
