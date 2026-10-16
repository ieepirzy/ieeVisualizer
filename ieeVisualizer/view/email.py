"""Email-safe HTML for a ViewSpec: ``render_email(view, profile="email-phone")``.

Email clients run no script, many ignore ``<style>`` blocks or media queries
(Gmail with non-Google accounts, Outlook for Windows) and Gmail shows no
inline SVG or ``data:`` image, so this output differs from ``render_view(...,
"html")`` (``docs/MOBILE_AND_EMAIL.md`` has the sources):

* every style is inline; layout is nested ``role="presentation"`` tables,
  a 576 px column with 12 px gutters, centred;
* KPI tiles and other columns are ``inline-block`` boxes at most half the
  column wide: two side by side on a tablet, one under another on a phone,
  with no media query (a client without ``inline-block`` stacks them too);
* figures and flows are PNG images (``<img width=... style="width:100%;
  max-width:...">``) rendered by the real renderers for the profile, so their
  text meets its minimum on the narrowest screen they are shown on; the
  caller attaches them (``EmailRender.images``; ``src`` is ``cid:<name>``
  unless ``image_src`` says otherwise). Each flow's text version is written
  out under it;
* a table too wide for the narrowest screen is written as one card per row
  (every cell, whole) and says so, instead of being cut or scrolled;
* ``color-scheme: light`` asks clients that honour it not to recolour; the
  images carry their own opaque background either way;
* ``EmailRender.text`` says the same in plain text (headline, every KPI line
  exactly as written, every table cell, each figure's text alternative, each
  flow's text version) for the ``text/plain`` part of the message
  (:func:`ieeVisualizer.view.message.build_message`).
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..render import RenderRequest, RenderWarning, Resolver, get_profile, render
from ..spec import SpecValidationError
from ..spec._common import ptr_join
from .draw import Group, Rect, esc, to_png
from .flow import flow_outline
from .html import markdown_html
from .layout import (
    KPI_SMALL_SIZES,
    KPI_VALUE_SIZES,
    TEXT_STYLES,
    _fit_number_text,
    _kpi_variants,
    fit_flow,
    percent_fraction_warning,
    table_cells,
    text_width,
)
from .limits import MAX_PNG_PIXELS, MAX_PNG_SIDE_PX
from .model import Card, FigureComponent, Flow, Grid, Kpi, Node, Note, Stack, Table, Text, ViewSpec
from .render import label_glyphs
from .text import NO_DATA, NULL_CELL, parse_markdown
from .validate import errors

#: A language tag (BCP 47 shape: letters, then ``-`` subtags); no spaces or CR/LF.
_LANG = re.compile(r"[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8}){0,6}")
#: SMTP lines may not exceed 998 bytes; HTML is broken at spaces before that.
MAX_LINE = 900

#: Content column (CSS px) on a wide screen, and the gutters around it.
COLUMN = 576
GUTTER = 12
#: A tile (KPI, figure in a grid) is never narrower than this on a wide screen.
MIN_TILE = 180
#: Gmail cuts a message whose HTML exceeds about 102 KB behind "View entire
#: message"; the rest is not shown in the mail view.
GMAIL_CLIP_BYTES = 102 * 1024
#: Extra room a value that must stay whole needs over its DejaVu Sans width
#: (:func:`whole_width`, the one measure behind every such decision).
#: Measured in Chromium at 13 px (normal and bold; dates, grouped numbers,
#: words, WWWWWW/MMMMMM, CJK): DejaVu Sans renders at 1.000 of this model;
#: Liberation Sans, FreeSans and WenQuanYi Zen Hei are narrower; Inter, the
#: widest fallback seen, reaches 1.047 on WWW/MMM. 6 % covers that.
SLACK = 1.06
#: Horizontal space a table card takes from its slot: padding, border, margin.
CARD_INSET = 30
#: ... and a KPI tile: padding and border.
KPI_INSET = 30
INK, MUTED, LINE, PANEL = "#1f2328", "#57606a", "#d0d7de", "#f6f8fa"
#: Every text container also breaks words longer than its box (URLs, ids),
#: which would otherwise widen the whole email past a phone's screen.
FONT = ("font-family:'DejaVu Sans',Verdana,Arial,sans-serif;overflow-wrap:anywhere;"
        "word-break:break-word;")
#: The font without the word breaking, for values that must stay whole.
PLAIN = "font-family:'DejaVu Sans',Verdana,Arial,sans-serif;"
#: A presentation table as wide as its slot. (No ``table-layout:fixed``: the
#: text inside already wraps, and the browser tests pass without it.)
TABLE = ('role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
         'style="width:100%;')
#: Smallest slot (CSS px) a component may get on the narrowest screen.
MIN_EMAIL_SLOT = 120.0
#: Plain-text lines are wrapped at spaces to this many characters (RFC 5322
#: recommends 78); a longer word (a URL) is never split.
TEXT_WIDTH = 76


@dataclass
class EmailRender:
    """``html`` plus the images it references (``{name: PNG bytes}``) and
    ``text``, the same content as plain text."""

    html: str
    images: dict[str, bytes] = field(default_factory=dict)
    warnings: list[RenderWarning] = field(default_factory=list)
    profile: str = ""
    text: str = ""
    #: What :func:`render_email` produced, as digests (:func:`seal`):
    #: :func:`~ieeVisualizer.view.message.build_message` refuses a render
    #: changed after it was made (or not made by ``render_email`` at all).
    seal: dict[str, str] = field(default_factory=dict, repr=False, compare=False)


def seal(e: EmailRender) -> dict[str, str]:
    """Digests of what a reader is shown and of the gate's verdict on it."""
    from ..render import GATING_CODES

    def h(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    return {
        "html": h(e.html.encode("utf-8", "surrogatepass")),
        "text": h(e.text.encode("utf-8", "surrogatepass")),
        "images": h("\n".join(f"{n}={h(c)}" for n, c in sorted(e.images.items()))
                    .encode("utf-8", "surrogatepass")),
        "profile": e.profile,
        "warnings": ",".join(sorted({w.code for w in e.warnings if w.code in GATING_CODES})),
    }


class _Email:
    def __init__(self, view: ViewSpec, profile: Any, resolver: Resolver | None,
                 image_src: Callable[[str], str]):
        self.view, self.p, self.resolver, self.src = view, profile, resolver, image_src
        self.images: dict[str, bytes] = {}
        self.warnings: list[RenderWarning] = []
        self.min = profile.min_html_text_px
        self.path = ""
        self.text: list[str] = []  # plain-text blocks, in reading order

    def say(self, *lines: str, indent: str = "") -> None:
        """A plain-text block: each line wrapped at spaces, never inside a word."""
        out = []
        for line in lines:
            out += textwrap.wrap(line, TEXT_WIDTH, subsequent_indent=indent,
                                 break_long_words=False, break_on_hyphens=False) or [""]
        self.text.append("\n".join(out))

    def px(self, size: float) -> float:
        return max(size, self.min)

    def node(self, n: Node, path: str, lo: float, hi: float) -> str:
        """``n`` in a slot ``lo`` CSS px wide on the narrowest screen and
        ``hi`` px on the widest."""
        if lo < MIN_EMAIL_SLOT:  # nesting ate the width: refuse, never draw into a sliver
            from ..spec import ValidationIssue

            raise SpecValidationError([ValidationIssue(path, "too_narrow", (
                f"on a {self.p.css_width:g} px wide screen ({self.p.name}) this component gets "
                f"{max(lo, 0):.0f} px; the minimum is {MIN_EMAIL_SLOT:g} px. Nest fewer cards "
                "or columns."))])
        if isinstance(n, Card):
            if n.title:
                self.say(n.title.upper())
            body = "".join(self.block(self.node(c, ptr_join(path, "children", i), lo - 26,
                                                hi - 26))
                           for i, c in enumerate(n.children))
            title = (f'<div style="{FONT}font-size:{self.px(16):g}px;font-weight:bold;'
                     f'color:{INK};margin:0 0 10px 0">{esc(n.title)}</div>' if n.title else "")
            return (f'<table {TABLE}border:1px solid {LINE};border-radius:8px">'
                    f'<tr><td style="padding:12px">{title}{body}</td></tr></table>')
        if isinstance(n, (Stack, Grid)):
            return self.columns(n, path, lo, hi)
        if isinstance(n, FigureComponent):
            return self.figure(n, path, lo, hi)
        if isinstance(n, Text):
            size, lh, bold, color = TEXT_STYLES.get(n.style, TEXT_STYLES["body"])
            self.say_prose(n.text)
            return self.prose(n.text, self.px(size), color, bold)
        if isinstance(n, Note):
            self.say_prose(("" if n.type == "footnote" else "Caveat: ") + n.text)
            if n.type == "footnote":
                return (f'<div style="border-top:1px solid {LINE};padding-top:6px">'
                        f'{self.prose(n.text, self.px(12), MUTED)}</div>')
            return (f'<table {TABLE}"><tr><td style="background:#fff8c5;border-left:4px solid '
                    f'#bf8700;padding:10px 12px">'
                    f'{self.prose("**Caveat:** " + n.text, self.px(13), INK)}</td></tr></table>')
        if isinstance(n, Kpi):
            self.path = path
            return self.kpi(n, lo)
        if isinstance(n, Table):
            return self.table(n, path, lo)
        return self.flow(n, path, lo)

    def say_prose(self, text: str) -> None:
        for kind, runs in parse_markdown(text):
            line = "".join(r.text for r in runs)
            if kind == "li":
                self.say("- " + line, indent="  ")
            else:
                self.say(line)

    def block(self, inner: str) -> str:
        return f'<div style="margin:0 0 16px 0">{inner}</div>\n'

    def prose(self, text: str, size: float, color: str, bold: bool = False) -> str:
        weight = "font-weight:bold;" if bold else ""
        html = (markdown_html(text).replace("<p>", '<p style="margin:0 0 8px 0">')
                .replace("<ul>", '<ul style="margin:0 0 8px 0;padding-left:20px">'))
        return (f'<div style="{FONT}font-size:{size:g}px;line-height:1.45;color:{color};'
                f'{weight}">{html}</div>')

    def columns(self, n: Stack | Grid, path: str, lo: float, hi: float) -> str:
        kids = [(c, ptr_join(path, "children", i)) for i, c in enumerate(n.children)]
        cols = (n.columns if isinstance(n, Grid) and isinstance(n.columns, int) else
                len(kids) if isinstance(n, Stack) and n.direction == "horizontal" else 1)
        cols = max(1, min(cols, len(kids), int(hi // MIN_TILE)))
        if cols == 1:
            return "".join(self.block(self.node(c, p, lo, hi)) for c, p in kids)
        tile = hi // min(cols, 2)  # at most two across: tiles stay wide enough to read
        inner_lo, inner_hi = min(tile, lo) - GUTTER, tile - GUTTER
        boxes = "".join(
            f'<div style="display:inline-block;vertical-align:top;width:100%;'
            f'max-width:{tile:g}px"><div style="padding:0 {GUTTER}px 16px 0;font-size:16px;'
            f'line-height:1.45">'
            f'{self.node(c, p, inner_lo, inner_hi)}</div></div>\n' for c, p in kids)
        return f'<div style="font-size:0;line-height:0">{boxes}</div>'

    def image(self, name: str, content: bytes, width: float, alt: str) -> str:
        # Content-ID form; the token is the image's own hash, so a name changes
        # whenever the picture does (other data behind the same view included)
        digest = hashlib.sha256(content).hexdigest()[:12]
        name = name.replace(".png", f".{digest}.png@ieevisualizer")
        self.images[name] = content
        return (f'<img src="{esc(self.src(name))}" width="{int(width)}" alt="{esc(alt)}" '
                f'style="display:block;width:100%;max-width:{int(width)}px;height:auto;'
                f'border:0;outline:none">')

    def figure(self, n: FigureComponent, path: str, lo: float, hi: float) -> str:
        spec = n.spec if n.spec is not None else self.view.figures[n.figure]
        pointer = ptr_join(path, "spec") if n.spec is not None else ptr_join("/figures", n.figure)
        aspect = max(n.aspect, self.p.aspect) if self.p.narrow else n.aspect
        req: RenderRequest = self.p.figure_request("png", aspect=aspect, display_width=lo)
        result = render(spec, req, resolver=self.resolver)
        self.warnings += [RenderWarning(w.code, w.message, pointer + w.path)
                          for w in result.warnings]
        theme = result.theme or {}
        if _dark(theme.get("name")):
            self.warnings.append(RenderWarning(
                "email_dark_figure",
                f"the figure is drawn with the dark theme {theme.get('name')!r} in an email that "
                "declares a light colour scheme; it shows as a dark block on a light page. Use "
                "a light theme for email.", pointer + "/theme"))
        alt = ". ".join(x for x in (spec.title or "Figure", figure_summary(spec, self.resolver))
                        if x)
        self.say(f"[Figure: {alt}]")
        return self.image(f"figure-{len(self.images)}.png", result.content, hi, alt)

    def kpi(self, n: Kpi, lo: float) -> str:
        room = lo - KPI_INSET
        iw = room / SLACK
        variants = _kpi_variants(n)
        small = tuple(s for s in KPI_SMALL_SIZES if s >= self.min)
        exact = variants[0][0]
        w_frac = percent_fraction_warning(n, self.path)
        if w_frac is not None:
            self.warnings.append(w_frac)
        self.say(f"{n.label}: {exact[0]}", *([f"  {exact[1]}"] if exact[1] else []),
                 *([f"  Source: {n.source}"] if n.source else []), indent="  ")
        if n.value is None:
            value = (f'<div style="{FONT}font-size:22px;font-style:italic;color:{MUTED};'
                     f'margin:4px 0">{NO_DATA}</div>')
        else:
            sizes = tuple(s for s in KPI_VALUE_SIZES if s >= self.min)
            lines, size = _fit_number_text(variants, 0, sizes, iw, True, True, small)
            self.compacted(variants, 0, lines, n)
            self.whole(lines, size, room, f"KPI {n.label!r}", True)
            value = (f'<div style="{PLAIN}font-size:{size:g}px;font-weight:bold;color:{INK};'
                     f'margin:4px 0;line-height:1.25;white-space:nowrap">'
                     f'{"<br>".join(map(esc, lines))}</div>')
        parts = [f'<div style="{FONT}font-size:{self.px(13):g}px;color:{MUTED}">'
                 f'{esc(n.label)}</div>', value]
        if variants[0][0][1]:
            lines, size = _fit_number_text(variants, 1, (self.px(12),), iw, False, False, small)
            self.compacted(variants, 1, lines, n)
            self.whole(lines, size, room, f"KPI {n.label!r} comparison")
            parts.append(f'<div style="{PLAIN}font-size:{size:g}px;color:{INK};'
                         f'white-space:nowrap">{"<br>".join(map(esc, lines))}</div>')
        if n.source:
            parts.append(f'<div style="{FONT}font-size:{self.px(12):g}px;color:{MUTED}">'
                         f'Source: {esc(n.source)}</div>')
        return (f'<table {TABLE}background:{PANEL};border:1px solid {LINE};'
                f'border-radius:8px"><tr><td style="padding:12px 14px">{"".join(parts)}'
                "</td></tr></table>")

    def compacted(self, variants: list, which: int, lines: list[str], n: Kpi) -> None:
        """A KPI number rounded to fewer digits to fit is a warning, never silent."""
        exact = variants[0][0][which]
        if "".join(lines).replace(" ", "") != exact.replace(" ", ""):
            self.warnings.append(RenderWarning(
                "kpi_value_compacted",
                f"KPI {n.label!r} is shown as '{' '.join(lines)}' instead of '{exact}': the "
                "exact number does not fit its tile at the minimum text size.", ""))

    def whole(self, values: list[str], size: float, room: float, what: str,
              bold: bool = False) -> None:
        """Values that must stay whole (never split) but are wider than the
        ``room`` they get on the narrowest screen: ``value_too_wide_for_screen``
        (they would run past the screen edge)."""
        wide = [v for v in values if whole_width(v, size, bold) > room]
        if wide:
            self.warnings.append(RenderWarning(
                "value_too_wide_for_screen",
                f"{what}: {len(wide)} value(s) need more than the {room:.0f} px they get on "
                f"a {self.p.css_width:g} px screen ({self.p.name}) and cannot be split: "
                + ", ".join(repr(v[:40]) for v in wide[:3]) + ". Nest fewer cards or "
                "columns, or shorten the values.", self.path))

    def table(self, n: Table, path: str, lo: float) -> str:
        ds = self.view.datasets[n.data]
        names, heads, rows, total = table_cells(n, ds, path)
        size = self.px(13)
        num = [ds.columns[c].dtype in ("int", "float") for c in names]
        # A cell value is never split: numbers, dates and ids would read as
        # other values. Data cells do not wrap (white-space:nowrap); a table is
        # used only when every column fits its widest value whole (and its
        # longest header word: headers wrap at spaces), with SLACK room
        # for a client's substitute font. Otherwise one card per row.
        want = sum(max([whole_width(w, size, True) for w in h.split(" ")]
                       + [whole_width(r[i], size) for r in rows]) + 16 * SLACK
                   for i, h in enumerate(heads))
        cell = f"{PLAIN}font-size:{size:g}px;padding:4px 8px;"
        cap = (f'<div style="{FONT}font-size:{self.px(15):g}px;font-weight:bold;color:{INK};'
               f'margin:0 0 6px 0">{esc(n.title)}</div>' if n.title else "")
        parts = [cap]
        if n.title:
            self.say(n.title.upper())
        for r in rows:  # one block per row, one "header: value" line per cell
            self.say(*(("- " if i == 0 else "  ") + f"{h}: {c}"
                       for i, (h, c) in enumerate(zip(heads, r))), indent="    ")
        if want <= lo:
            th = "".join(f'<th align="{"right" if num[i] else "left"}" style="{cell}'
                         f'background:#eaeef2;color:{INK}">{esc(h)}</th>'
                         for i, h in enumerate(heads))
            trs = "".join(
                f'<tr style="background:{PANEL if k % 2 else "#ffffff"}">' + "".join(
                    f'<td align="{"right" if num[i] else "left"}" style="{cell}'
                    f'white-space:nowrap;color:'
                    f'{MUTED if s == NULL_CELL else INK}">{esc(s)}</td>'
                    for i, s in enumerate(r)) + "</tr>\n" for k, r in enumerate(rows))
            parts.append(f'<table role="presentation" width="100%" cellpadding="0" '
                         f'cellspacing="0" border="0" style="border-collapse:collapse">'
                         f"<tr>{th}</tr>{trs}</table>")
        else:
            parts.append(f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED};'
                         f'margin:0 0 6px 0">One card per row: the {len(heads)} columns do not '
                         f"fit side by side in the {lo:.0f} px this table gets on a phone "
                         f"screen.</div>")
            cards = []
            # in a card a text value may wrap (a long URL); numbers, dates and
            # booleans stay whole, and must fit the card
            whole = [ds.columns[c].dtype != "str" for c in names]
            room = min(COLUMN // 2, lo) - CARD_INSET
            self.path = path
            self.whole([r[i] for r in rows for i in range(len(names)) if whole[i]], size, room,
                       f"table {n.title or n.data!r}")
            for r in rows:
                fields = "".join(
                    f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED}">{esc(h)}</div>'
                    f'<div style="{PLAIN if whole[i] else FONT}font-size:{size:g}px;color:'
                    f'{MUTED if s == NULL_CELL else INK};margin:0 0 6px 0;'
                    f'{"white-space:nowrap;" if whole[i] else ""}">{esc(s)}</div>'
                    for i, (h, s) in enumerate(zip(heads, r)))
                cards.append(f'<div style="display:inline-block;vertical-align:top;width:100%;'
                             f'max-width:{COLUMN // 2}px"><div style="border:1px solid {LINE};'
                             f'border-radius:6px;padding:8px 10px;margin:0 8px 8px 0;'
                             f'line-height:1.45">{fields}</div></div>\n')
            parts.append(f'<div style="font-size:0;line-height:0">{"".join(cards)}</div>')
        if len(rows) < total:
            self.say(f"Showing {len(rows)} of {total} rows")
            parts.append(f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED}">'
                         f"Showing {len(rows)} of {total} rows</div>")
        return "".join(parts)

    def flow(self, f: Flow, path: str, lo: float) -> str:
        f2, items, gw, gh, order, s = fit_flow(f, lo, self.p.min_text_px, path, self.warnings)
        w, h = max(gw * s, 1.0), max(gh * s, 1.0)
        # flow_text_too_small (from fit_flow) already reports small labels
        px_w, px_h = round(w * self.p.scale), round(h * self.p.scale)
        if max(px_w, px_h) > MAX_PNG_SIDE_PX or px_w * px_h > MAX_PNG_PIXELS:
            from ..render import RenderError
            from ..spec import ValidationIssue

            raise RenderError([ValidationIssue(path, "view_too_large", (
                f"the flow's image would be {px_w} x {px_h} px; the limits are "
                f"{MAX_PNG_SIDE_PX} px a side and {MAX_PNG_PIXELS / 1e6:g} megapixels. Split "
                "the flow or use a grid layout."))])
        png = to_png([Rect(0, 0, w, h, fill="#ffffff"), Group(items, scale=s)], w, h,
                     self.p.scale, lambda e, pw, ph: None)
        self.warnings += label_glyphs(items, path)
        title = (f'<div style="{FONT}font-size:{self.px(15):g}px;font-weight:bold;color:{INK};'
                 f'margin:0 0 6px 0">{esc(f.title)}</div>' if f.title else "")
        lis = "".join(f"<li>{esc(line.split('. ', 1)[1])}</li>"
                      for line in flow_outline(f2, order))
        desc = f"<p>{esc(f.description)}</p>" if f.description else ""
        text = (f'<div style="{FONT}font-size:{self.px(13):g}px;color:{INK};margin-top:8px">'
                f"<strong>Text version</strong>{desc}"
                f'<ol style="margin:4px 0 0 0;padding-left:22px">{lis}</ol></div>')
        img = self.image(f"flow-{len(self.images)}.png", png, w,
                         (f.title or "Flow diagram") + " (text version below)")
        self.say(f"[Flow diagram: {f.title or 'untitled'}]", *([f.description] if f.description
                                                               else []),
                 *flow_outline(f2, order), indent="   ")
        return title + img + text


def render_email(view: ViewSpec, profile: str = "email-phone", *,
                 resolver: Resolver | None = None,
                 image_src: Callable[[str], str] | None = None) -> EmailRender:
    """Email-safe HTML for ``view`` (see the module docstring). ``profile``
    is the narrowest screen the email must be legible on; ``image_src``
    maps an image name to its ``src`` (default ``cid:<name>``)."""
    prof = get_profile(profile)
    errs = errors(view)
    lang = view.metadata.get("lang", "en")
    if not (isinstance(lang, str) and _LANG.fullmatch(lang)):
        from ..spec import ValidationIssue

        errs.append(ValidationIssue("/metadata/lang", "invalid_value",
                                    f"lang must be a language tag such as 'en' or 'fi-FI', got "
                                    f"{lang!r}."))
    if errs:
        raise SpecValidationError(errs)
    e = _Email(view, prof, resolver, image_src or (lambda name: f"cid:{name}"))
    # the profile width is the narrowest screen; the content is that minus the
    # outer gutters (296 px on a 320 px phone)
    lo = min(prof.css_width - 2 * GUTTER, COLUMN)
    body = e.node(view.root, "/root", lo, COLUMN)
    title = (f'<h1 style="{FONT}font-size:24px;line-height:1.25;color:{INK};margin:0 0 16px 0">'
             f"{esc(view.title)}</h1>" if view.title else "")
    html = (
        f'<!DOCTYPE html>\n<html lang="{esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        '<meta name="supported-color-schemes" content="light">\n'
        f"<title>{esc(view.title or 'Report')}</title>\n</head>\n"
        '<body style="margin:0;padding:0;background:#ffffff;-webkit-text-size-adjust:100%;'
        'text-size-adjust:100%">\n'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff"><tr><td align="center" '
        f'style="padding:16px {GUTTER}px">\n'
        f'<table role="presentation" width="{COLUMN}" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:100%;max-width:{COLUMN}px"><tr><td align="left">'
        f"{title}\n{body}</td></tr></table>\n</td></tr></table>\n</body>\n</html>\n")
    html = "\n".join(_short_lines(line) for line in html.split("\n"))
    if view.title:
        e.text.insert(0, view.title + "\n" + "=" * min(len(view.title), TEXT_WIDTH))
    text = "\n\n".join(e.text) + "\n"
    if not prof.narrow:
        e.warnings.append(RenderWarning(
            "email_not_phone_safe",
            f"laid out for {prof.css_width:g} px ({prof.name}): read on a phone (about 320 px) "
            f"its figure text shrinks to {12 * 320 / prof.css_width:.1f} px. Use email-phone "
            "for email anyone may read on a phone.", ""))
    size = len(html.encode("utf-8"))
    if size > GMAIL_CLIP_BYTES:
        e.warnings.append(RenderWarning(
            "email_clipped_by_gmail",
            f"the email HTML is {size // 1024} KB; Gmail hides everything after about "
            f"{GMAIL_CLIP_BYTES // 1024} KB behind 'View entire message'. Split the view or "
            "show fewer table rows.", ""))
    out = EmailRender(html=html, images=e.images, warnings=e.warnings, profile=prof.name,
                      text=text)
    out.seal = seal(out)
    return out


def _short_lines(line: str) -> str:
    """``line`` broken into lines of at most :data:`MAX_LINE` UTF-8 bytes at
    spaces where a line break means the same: in text, or between a tag's
    attributes - never inside a quoted attribute value (a break inside
    ``style="font-family:'DejaVu Sans'"`` would invalidate the style). A
    longer run with no such space stays, for the transfer encoding to fold."""
    if len(line.encode("utf-8")) <= MAX_LINE:
        return line
    out, start, size, last, in_tag, quote = [], 0, 0, -1, False, ""
    for k, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif in_tag and ch in "\"'":
            quote = ch
        elif ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif ch == " ":
            last = k
        size += len(ch.encode("utf-8"))
        if size > MAX_LINE and last >= start:
            out.append(line[start:last])
            start = last + 1
            size = len(line[start:k + 1].encode("utf-8"))
            last = -1
    return "\n".join(out + [line[start:]])


def _dark(theme_name: str | None) -> bool:
    from ..themes import resolve_for_render
    from ..themes.color import relative_luminance

    theme, _ = resolve_for_render(theme_name)
    return theme.background is not None and relative_luminance(theme.background) < 0.2


def figure_summary(spec: Any, resolver: Resolver | None = None, limit: int = 400) -> str:
    """A text alternative carrying the figure's measured values (for an email
    whose images are blocked): per layer, its name, how many values and their
    range with the unit. Only value channels are summarised - y (x for
    horizontal bars), the samples of a histogram, a heatmap's colour, a
    quiver's vector magnitude (u, v and w in 3D); in 3D only a colour channel,
    since x, y and z are all positions - never positions. References are resolved
    with ``resolver`` first; ``""`` when nothing can be summarised."""
    import math

    from ..render import prepare
    from .text import format_number

    meta = spec.metadata.get("template") if isinstance(spec.metadata, dict) else None
    bounds: dict[int, str] = {}  # layer index -> which bound of a band it draws
    for band in (meta or {}).get("bands") or []:
        if isinstance(band, dict):
            bounds[band.get("lower_layer")] = "lower bound"
            bounds[band.get("upper_layer")] = "upper bound"
    names = [m.name for m in spec.layers]
    try:
        spec = prepare(spec, resolver).spec
    except Exception:  # noqa: BLE001 - the render itself reports why
        return ""
    shared = _boundary_copies(spec) if ((meta or {}).get("forecast") or {}).get(
        "shared_boundary") else {}
    parts = []
    for k, m in enumerate(spec.layers):
        snap = spec.datasets.get(m.data) if isinstance(m.data, str) else None
        cols = getattr(snap, "columns", None)
        enc = getattr(m, "encoding", {}) or {}
        if cols is None:
            continue
        # the observed point copied into a forecast layer so the lines join is
        # an observation, not a forecast value: it is not counted twice
        skip = shared.get(m.data, set()) if getattr(m, "role", None) == "forecast" else set()
        what = ""
        parts += _interval_summary(m, cols, skip)
        if m.type == "quiver" and "u" in enc and "v" in enc:
            comps = [cols[enc[c].field] for c in ("u", "v", "w") if c in enc]
            vals = [math.sqrt(sum(c * c for c in row)) for row in zip(*(c.values for c in comps))
                    if None not in row]
            unit, what, label = comps[0].unit, "vector magnitude, ", "vectors"
        else:
            if spec.coordinate_system == "cartesian3d":
                ch = "color"  # x, y and z are all positions in 3D
            else:
                ch = {"histogram": "x", "heatmap": "color"}.get(
                    m.type, "x" if getattr(m, "orientation", None) == "horizontal" else "y")
            col = cols.get(enc[ch].field) if ch in enc else None
            if col is None or col.dtype not in ("int", "float"):
                continue
            vals = [x for i, x in enumerate(col.values) if x is not None and i not in skip]
            unit = col.unit
            label = enc[ch].title or enc[ch].field
        if not vals:
            continue
        name = m.name or label
        if k in bounds:  # both bounds of a band share one (legend) name
            name = f"{name}, {bounds[k]}"
        elif m.name and names.count(m.name) > 1:  # never two summaries under one name
            name = f"{name} ({label})"
        unit = f" {unit}" if unit else ""
        parts.append(f"{name}: {what}{len(vals)} values from {format_number(min(vals))} to "
                     f"{format_number(max(vals))}{unit}")
    text = "; ".join(parts)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _interval_summary(m: Any, cols: Any, skip: set) -> list[str]:
    """An interval's bounds (band, errorbar, y_lower/y_upper), named as its
    legend entry ("min-max range (forecast), lower bound: ...")."""
    from ..render.mpl.style import legend_label
    from ..spec.intervals import has_interval, mark_interval_label
    from .text import format_number

    enc = getattr(m, "encoding", {}) or {}
    if not has_interval(m) or "y_lower" not in enc or "y_upper" not in enc:
        return []
    lo, hi = cols.get(enc["y_lower"].field), cols.get(enc["y_upper"].field)
    if lo is None or hi is None:
        return []
    rows = [(a, b) for i, (a, b) in enumerate(zip(lo.values, hi.values))
            if a is not None and b is not None and i not in skip]
    if not rows:
        return []
    text = mark_interval_label(m)
    name = legend_label(f"{m.name}: {text}" if m.name else text, None, m.role)
    unit = f" {lo.unit}" if lo.unit else ""
    return [f"{name}, {which}: {len(rows)} values from {format_number(min(v))} to "
            f"{format_number(max(v))}{unit}"
            for which, v in (("lower bound", [a for a, _ in rows]),
                             ("upper bound", [b for _, b in rows]))]


def _boundary_copies(spec: Any) -> dict[str, set[int]]:
    """``{dataset: row indexes}`` of the rows of forecast layers' datasets that
    are copies of an observed row (the timeseries template's shared boundary,
    ``connect_forecast``): equal in every column to a row of an observed
    layer's dataset."""
    def rows(name: str) -> list[tuple]:
        cols = getattr(spec.datasets.get(name), "columns", None) or {}
        keys = sorted(cols)
        return list(zip(*(tuple(cols[c].values) for c in keys))) if keys else []

    fc = {m.data for m in spec.layers if getattr(m, "role", None) == "forecast"
          and isinstance(m.data, str)}
    obs = {m.data for m in spec.layers if getattr(m, "role", None) != "forecast"
           and isinstance(m.data, str)} - fc
    seen = {r for name in obs for r in rows(name)}
    return {name: {i for i, r in enumerate(rows(name)) if r in seen} for name in fc}


def whole_width(text: str, size: float, bold: bool = False) -> float:
    """CSS px a value that must not be split needs at ``size``: its DejaVu
    width plus :data:`SLACK` for a substitute font. Every keep-whole decision
    (table or cards, KPI lines, card values) compares this with the room the
    value really gets."""
    return text_width(text[:200], size, bold) * SLACK
