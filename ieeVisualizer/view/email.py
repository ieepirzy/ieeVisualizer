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
  images carry their own opaque background either way.
"""

from __future__ import annotations

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
    table_cells,
    text_width,
)
from .model import Card, FigureComponent, Flow, Grid, Kpi, Node, Note, Stack, Table, Text, ViewSpec
from .text import NO_DATA, NULL_CELL
from .validate import errors

#: Content column (CSS px) on a wide screen, and the gutters around it.
COLUMN = 576
GUTTER = 12
#: A tile (KPI, figure in a grid) is never narrower than this on a wide screen.
MIN_TILE = 180
#: Gmail cuts a message whose HTML exceeds about 102 KB behind "View entire
#: message"; the rest is not shown in the mail view.
GMAIL_CLIP_BYTES = 102 * 1024
#: Email clients may substitute Verdana/Arial for DejaVu Sans: text measured
#: with DejaVu metrics gets this much extra room.
FONT_SLACK = 1.1
INK, MUTED, LINE, PANEL = "#1f2328", "#57606a", "#d0d7de", "#f6f8fa"
FONT = "font-family:'DejaVu Sans',Verdana,Arial,sans-serif;"


@dataclass
class EmailRender:
    """``html`` plus the images it references (``{name: PNG bytes}``)."""

    html: str
    images: dict[str, bytes] = field(default_factory=dict)
    warnings: list[RenderWarning] = field(default_factory=list)
    profile: str = ""


class _Email:
    def __init__(self, view: ViewSpec, profile: Any, resolver: Resolver | None,
                 image_src: Callable[[str], str]):
        self.view, self.p, self.resolver, self.src = view, profile, resolver, image_src
        self.images: dict[str, bytes] = {}
        self.warnings: list[RenderWarning] = []
        self.min = profile.min_html_text_px

    def px(self, size: float) -> float:
        return max(size, self.min)

    def node(self, n: Node, path: str, lo: float, hi: float) -> str:
        """``n`` in a slot ``lo`` CSS px wide on the narrowest screen and
        ``hi`` px on the widest."""
        if isinstance(n, Card):
            body = "".join(self.block(self.node(c, ptr_join(path, "children", i), lo - 26,
                                                hi - 26))
                           for i, c in enumerate(n.children))
            title = (f'<div style="{FONT}font-size:{self.px(16):g}px;font-weight:bold;'
                     f'color:{INK};margin:0 0 10px 0">{esc(n.title)}</div>' if n.title else "")
            return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    f'border="0" style="border:1px solid {LINE};border-radius:8px">'
                    f'<tr><td style="padding:12px">{title}{body}</td></tr></table>')
        if isinstance(n, (Stack, Grid)):
            return self.columns(n, path, lo, hi)
        if isinstance(n, FigureComponent):
            return self.figure(n, path, lo, hi)
        if isinstance(n, Text):
            size, lh, bold, color = TEXT_STYLES.get(n.style, TEXT_STYLES["body"])
            return self.prose(n.text, self.px(size), color, bold)
        if isinstance(n, Note):
            if n.type == "footnote":
                return (f'<div style="border-top:1px solid {LINE};padding-top:6px">'
                        f'{self.prose(n.text, self.px(12), MUTED)}</div>')
            return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    f'border="0"><tr><td style="background:#fff8c5;border-left:4px solid '
                    f'#bf8700;padding:10px 12px">'
                    f'{self.prose("**Caveat:** " + n.text, self.px(13), INK)}</td></tr></table>')
        if isinstance(n, Kpi):
            return self.kpi(n, lo)
        if isinstance(n, Table):
            return self.table(n, path, lo)
        return self.flow(n, path, lo)

    def block(self, inner: str) -> str:
        return f'<div style="margin:0 0 16px 0">{inner}</div>'

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
            f'{self.node(c, p, inner_lo, inner_hi)}</div></div>' for c, p in kids)
        return f'<div style="font-size:0;line-height:0">{boxes}</div>'

    def image(self, name: str, content: bytes, width: float, alt: str) -> str:
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
        alt = spec.title or "Figure"
        return self.image(f"figure-{len(self.images)}.png", result.content, hi, alt)

    def kpi(self, n: Kpi, lo: float) -> str:
        iw = (lo - 30) / FONT_SLACK
        variants = _kpi_variants(n)
        small = tuple(s for s in KPI_SMALL_SIZES if s >= self.min)
        if n.value is None:
            value = (f'<div style="{FONT}font-size:22px;font-style:italic;color:{MUTED};'
                     f'margin:4px 0">{NO_DATA}</div>')
        else:
            sizes = tuple(s for s in KPI_VALUE_SIZES if s >= self.min)
            lines, size = _fit_number_text(variants, 0, sizes, iw, True, True, small)
            value = (f'<div style="{FONT}font-size:{size:g}px;font-weight:bold;color:{INK};'
                     f'margin:4px 0;line-height:1.25">{"<br>".join(map(esc, lines))}</div>')
        parts = [f'<div style="{FONT}font-size:{self.px(13):g}px;color:{MUTED}">'
                 f'{esc(n.label)}</div>', value]
        if variants[0][0][1]:
            lines, size = _fit_number_text(variants, 1, (self.px(12),), iw, False, False, small)
            parts.append(f'<div style="{FONT}font-size:{size:g}px;color:{INK}">'
                         f'{"<br>".join(map(esc, lines))}</div>')
        if n.source:
            parts.append(f'<div style="{FONT}font-size:{self.px(12):g}px;color:{MUTED}">'
                         f'Source: {esc(n.source)}</div>')
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'border="0" style="background:{PANEL};border:1px solid {LINE};'
                f'border-radius:8px"><tr><td style="padding:12px 14px">{"".join(parts)}'
                "</td></tr></table>")

    def table(self, n: Table, path: str, lo: float) -> str:
        ds = self.view.datasets[n.data]
        names, heads, rows, total = table_cells(n, ds, path)
        size = self.px(13)
        num = [ds.columns[c].dtype in ("int", "float") for c in names]
        want = sum(max([text_width(h[:200], size, True)] + [text_width(r[i][:200], size)
                                                              for r in rows]) + 16
                   for i, h in enumerate(heads)) * FONT_SLACK
        cell = f"{FONT}font-size:{size:g}px;padding:4px 8px;"
        cap = (f'<div style="{FONT}font-size:{self.px(15):g}px;font-weight:bold;color:{INK};'
               f'margin:0 0 6px 0">{esc(n.title)}</div>' if n.title else "")
        parts = [cap]
        if want <= lo:
            th = "".join(f'<th align="{"right" if num[i] else "left"}" style="{cell}'
                         f'background:#eaeef2;color:{INK}">{esc(h)}</th>'
                         for i, h in enumerate(heads))
            trs = "".join(
                f'<tr style="background:{PANEL if k % 2 else "#ffffff"}">' + "".join(
                    f'<td align="{"right" if num[i] else "left"}" style="{cell}color:'
                    f'{MUTED if s == NULL_CELL else INK}">{esc(s)}</td>'
                    for i, s in enumerate(r)) + "</tr>" for k, r in enumerate(rows))
            parts.append(f'<table role="presentation" width="100%" cellpadding="0" '
                         f'cellspacing="0" border="0" style="border-collapse:collapse">'
                         f"<tr>{th}</tr>{trs}</table>")
        else:
            parts.append(f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED};'
                         f'margin:0 0 6px 0">One card per row: the {len(heads)} columns do not '
                         f"fit side by side on a {lo:.0f} px wide screen.</div>")
            cards = []
            for r in rows:
                fields = "".join(
                    f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED}">{esc(h)}</div>'
                    f'<div style="{FONT}font-size:{size:g}px;color:'
                    f'{MUTED if s == NULL_CELL else INK};margin:0 0 6px 0;'
                    f'word-break:break-word">{esc(s)}</div>' for h, s in zip(heads, r))
                cards.append(f'<div style="display:inline-block;vertical-align:top;width:100%;'
                             f'max-width:{COLUMN // 2}px"><div style="border:1px solid {LINE};'
                             f'border-radius:6px;padding:8px 10px;margin:0 8px 8px 0;'
                             f'line-height:1.45">{fields}</div></div>')
            parts.append(f'<div style="font-size:0;line-height:0">{"".join(cards)}</div>')
        if len(rows) < total:
            parts.append(f'<div style="{FONT}font-size:{self.min:g}px;color:{MUTED}">'
                         f"Showing {len(rows)} of {total} rows</div>")
        return "".join(parts)

    def flow(self, f: Flow, path: str, lo: float) -> str:
        f2, items, gw, gh, order, s = fit_flow(f, lo, self.p.min_text_px, path, self.warnings)
        w, h = max(gw * s, 1.0), max(gh * s, 1.0)
        png = to_png([Rect(0, 0, w, h, fill="#ffffff"), Group(items, scale=s)], w, h,
                     self.p.scale, lambda e, pw, ph: None)
        title = (f'<div style="{FONT}font-size:{self.px(15):g}px;font-weight:bold;color:{INK};'
                 f'margin:0 0 6px 0">{esc(f.title)}</div>' if f.title else "")
        lis = "".join(f"<li>{esc(line.split('. ', 1)[1])}</li>"
                      for line in flow_outline(f2, order))
        desc = f"<p>{esc(f.description)}</p>" if f.description else ""
        text = (f'<div style="{FONT}font-size:{self.px(13):g}px;color:{INK};margin-top:8px">'
                f"<strong>Text version</strong>{desc}"
                f'<ol style="margin:4px 0 0 0;padding-left:22px">{lis}</ol></div>')
        img = self.image(f"flow-{len(self.images)}.png", png, w, f.title or "Flow diagram")
        return title + img + text


def render_email(view: ViewSpec, profile: str = "email-phone", *,
                 resolver: Resolver | None = None,
                 image_src: Callable[[str], str] | None = None) -> EmailRender:
    """Email-safe HTML for ``view`` (see the module docstring). ``profile``
    is the narrowest screen the email must be legible on; ``image_src``
    maps an image name to its ``src`` (default ``cid:<name>``)."""
    prof = get_profile(profile)
    errs = errors(view)
    if errs:
        raise SpecValidationError(errs)
    e = _Email(view, prof, resolver, image_src or (lambda name: f"cid:{name}"))
    lo = min(prof.css_width, COLUMN)
    body = e.node(view.root, "/root", lo, COLUMN)
    title = (f'<h1 style="{FONT}font-size:24px;line-height:1.25;color:{INK};margin:0 0 16px 0">'
             f"{esc(view.title)}</h1>" if view.title else "")
    lang = view.metadata.get("lang") if isinstance(view.metadata.get("lang"), str) else "en"
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
        f"{title}{body}</td></tr></table>\n</td></tr></table>\n</body>\n</html>\n")
    size = len(html.encode("utf-8"))
    if size > GMAIL_CLIP_BYTES:
        e.warnings.append(RenderWarning(
            "email_clipped_by_gmail",
            f"the email HTML is {size // 1024} KB; Gmail hides everything after about "
            f"{GMAIL_CLIP_BYTES // 1024} KB behind 'View entire message'. Split the view or "
            "show fewer table rows.", ""))
    return EmailRender(html=html, images=e.images, warnings=e.warnings, profile=prof.name)
