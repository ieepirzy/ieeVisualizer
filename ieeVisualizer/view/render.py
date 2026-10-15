"""``render_view(view, format)``: SVG, self-contained HTML or PNG bytes.

* **svg** - one SVG document. Native SVG for text, KPI tiles, tables and
  flows; each figure is rendered by the real FigureSpec pipeline
  (:func:`ieeVisualizer.render.render`, so the matplotlib adapter) to SVG and
  nested as an ``<svg>`` element. Its ids are prefixed per placement
  (``v<n>-``) so two figures never share an id, and its RDF ``<metadata>``
  block is dropped, as is its ``<style>`` (scoped to the figure instead, see
  :func:`nest_parts`).
* **html** - a static page: native HTML text/tables/KPIs, inline SVG for
  figures and flows, one ``<style>`` block. No script, no external asset, no
  link; every string is escaped. Flows get a text version in ``<details>``.
* **png** - the same display list painted on a matplotlib canvas, figures
  rendered to PNG by the real renderer and placed pixel-exact. No SVG
  rasteriser (and no new dependency) is involved.

Output is deterministic: the same view, width and library versions give the
same bytes.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET  # noqa: N817
from typing import Any

from ..render import RenderError, RenderRequest, RenderResult, RenderWarning, Resolver, render
from ..spec import SpecValidationError, ValidationIssue
from .draw import Embed, Group, Label, esc, num, to_png, to_svg
from .layout import NARROW_MIN_SLOT, layout_view
from .limits import MAX_PNG_PIXELS, MAX_PNG_SIDE_PX
from .model import ViewSpec
from .validate import errors, too_narrow_at

RENDERER_NAME = "ieeVisualizer.view"
RENDERER_VERSION = "1.0.0"
VIEW_FORMATS = {"svg": "image/svg+xml", "html": "text/html", "png": "image/png"}

_SVG = "{http://www.w3.org/2000/svg}"
_XLINK = "{http://www.w3.org/1999/xlink}"
_URL_REF = re.compile(r"url\(#([^)]+)\)")
_STAR_RULE = re.compile(r"\s*\*\s*\{([^{}]*)\}\s*")


class FigureRenderer:
    """Renders each embedded figure once per (pointer, size, format)."""

    def __init__(self, resolver: Resolver | None, profile: Any = None):
        self.resolver = resolver
        self.profile = profile
        self.cache: dict[tuple, Any] = {}
        self.warnings: list[RenderWarning] = []

    def render(self, e: Embed, fmt: str, w_px: float, h_px: float, dpi: float = 96.0
               ) -> RenderResult:
        key = (e.pointer, fmt, round(w_px, 2), round(h_px, 2), dpi)
        if key not in self.cache:
            prof = {} if self.profile is None else {"profile": self.profile.name,
                                                    "display_width": float(w_px)}
            req = RenderRequest(format=fmt, width=w_px / 96, height=h_px / 96, units="in",
                                dpi=dpi, **prof)
            try:
                result = render(e.spec, req, resolver=self.resolver)
            except UnicodeError as exc:  # backstop; validation rejects such strings
                raise _figure_error(e, f"the figure's text cannot be encoded: {exc}") from None
            self.warnings.extend(RenderWarning(w.code, w.message, e.pointer + w.path)
                                 for w in result.warnings if w not in self.warnings)
            self.cache[key] = result
        return self.cache[key]

    def svg_parts(self, e: Embed) -> tuple[str, str]:
        """``(viewBox, inner markup)`` of the figure's SVG, ids prefixed."""
        try:
            return nest_parts(self.render(e, "svg", e.w, e.h).content, f"v{e.index}-")
        except ET.ParseError as exc:  # backstop; validation rejects XML-illegal text
            raise _figure_error(e, f"the figure's SVG is not well-formed XML ({exc}); a "
                                "string in the figure probably holds a control "
                                "character.") from None

    def nested_svg(self, e: Embed) -> str:
        vb, inner = self.svg_parts(e)
        return (f'<svg x="{num(e.x)}" y="{num(e.y)}" width="{num(e.w)}" height="{num(e.h)}" '
                f'viewBox="{vb}" data-figure="{esc(e.pointer)}">{inner}</svg>')

    def png_array(self, e: Embed, w: int, h: int):
        from matplotlib.image import imread

        res = self.render(e, "png", e.w, e.h, dpi=96 * w / e.w)
        return imread(io.BytesIO(res.content), format="png")


def _figure_error(e: Embed, msg: str) -> RenderError:
    return RenderError([ValidationIssue(e.pointer, "figure_render_failed", msg)])


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _attr_name(k: str) -> str | None:
    if k.startswith(_XLINK):
        return "xlink:" + k[len(_XLINK):]
    return None if k.startswith("{") else k


def nest_parts(svg: bytes, prefix: str) -> tuple[str, str]:
    """Parse matplotlib's SVG output and re-serialise its body with every id
    (and every ``#id`` / ``url(#id)`` reference) prefixed. Comments, the
    DOCTYPE and ``<metadata>`` are dropped; text and attributes re-escaped.

    A ``<style>`` block would apply to the whole page the figure is placed
    in, so none is copied. matplotlib's only one, ``*{stroke-linejoin: round;
    stroke-linecap: butt}``, is turned into the same (inherited) properties
    on a ``<g>`` around the figure, which styles exactly the figure."""
    root = ET.fromstring(svg)
    ids = {el.get("id") for el in root.iter() if el.get("id")}

    def fix(v: str) -> str:
        if v.startswith("#") and v[1:] in ids:
            return "#" + prefix + v[1:]
        return _URL_REF.sub(lambda m: f"url(#{prefix}{m.group(1)})"
                            if m.group(1) in ids else m.group(0), v)

    out: list[str] = []
    scoped: list[str] = []

    def ser(el: ET.Element) -> None:
        tag = _local(el.tag)
        if tag == "style":
            m = _STAR_RULE.fullmatch(el.text or "")
            if m:
                scoped.extend(d.strip() for d in m.group(1).split(";") if d.strip())
            return
        if tag == "metadata" or not el.tag.startswith(_SVG):
            return
        attrs = []
        for k, v in el.attrib.items():
            name = _attr_name(k)
            if name is None:
                continue
            v = prefix + v if name == "id" else fix(v)
            attrs.append(f' {name}="{esc(v)}"')
        out.append(f"<{tag}{''.join(attrs)}>")
        if el.text:
            out.append(esc(el.text))
        for c in el:
            ser(c)
            if c.tail and c.tail.strip():
                out.append(esc(c.tail))
        out.append(f"</{tag}>")

    for child in root:
        ser(child)
    inner = "".join(out)
    if scoped:
        inner = f'<g style="{esc(";".join(scoped))}">{inner}</g>'
    return root.get("viewBox", "0 0 100 100"), inner


def render_view(view: ViewSpec, format: str = "svg", *, width: float | None = None,
                scale: float | None = None, resolver: Resolver | None = None,
                profile: str | None = None) -> RenderResult:
    """Render ``view`` ``width`` CSS px wide (default 960). ``scale`` is the
    PNG pixel ratio (default 2: 1920 px wide for the default width). A
    ``profile`` (e.g. ``"email-phone"``, see
    :mod:`ieeVisualizer.render.profiles`) sets both, keeps text at its
    minimum size, stacks columns on a narrow profile and reports text below
    the minimum as warnings. Raises :class:`SpecValidationError` if
    :func:`validate_view` reports errors. For email, see
    :func:`ieeVisualizer.view.render_email`."""
    prof = None
    if profile is not None:
        from ..render.profiles import get_profile

        if width is not None or scale is not None:
            raise ValueError("a profile sets width and scale; pass one or the other.")
        prof = get_profile(profile)
        width, scale = prof.css_width, prof.scale
    width = 960.0 if width is None else width
    scale = 2.0 if scale is None else scale
    if format not in VIEW_FORMATS:
        raise ValueError(f"render_view cannot produce {format!r}; use one of "
                         f"{', '.join(VIEW_FORMATS)} (PDF is not supported for views yet).")
    if not (isinstance(width, (int, float)) and 200 <= width <= 4000):
        raise ValueError(f"width must be between 200 and 4000 px, got {width!r}.")
    if not (isinstance(scale, (int, float)) and 0.5 <= scale <= 4):
        raise ValueError(f"scale must be between 0.5 and 4, got {scale!r}.")
    errs = errors(view)
    if errs:
        raise SpecValidationError(errs)
    min_slot = NARROW_MIN_SLOT if prof is not None and prof.narrow else None
    narrow = too_narrow_at(view, width, min_slot)  # validation checked the default width only
    if narrow is not None:
        raise SpecValidationError([narrow])
    figs = FigureRenderer(resolver, prof)
    layout_warnings: list[RenderWarning] = []
    if format == "html":
        from .html import render_html

        content = render_html(view, figs, width).encode("utf-8")
    else:
        items, height, _ = layout_view(view, width, prof, layout_warnings)
        if prof is not None:
            layout_warnings += small_text(items, prof)
        if format == "svg":
            content = to_svg(items, width, height, figs.nested_svg, view.title).encode("utf-8")
        else:
            px_w, px_h = round(width * scale), round(height * scale)
            if max(width, height) * scale > MAX_PNG_SIDE_PX:
                raise RenderError([ValidationIssue(
                    "", "view_too_large",
                    f"the view is {round(height)} px tall; at scale {scale} the PNG would be "
                    f"over {MAX_PNG_SIDE_PX} px. Render HTML or SVG, lower scale, or split "
                    "the view.")])
            if px_w * px_h > MAX_PNG_PIXELS:  # memory follows the area, not the side
                raise RenderError([ValidationIssue(
                    "", "view_too_large",
                    f"at width {width} and scale {scale} the PNG would be {px_w} x {px_h} = "
                    f"{px_w * px_h / 1e6:.1f} megapixels; the limit is "
                    f"{MAX_PNG_PIXELS / 1e6:g}. Render HTML or SVG, lower width or scale, or "
                    "split the view.")])
            content = to_png(items, width, height, scale, figs.png_array)
    return RenderResult(content=content, format=format, media_type=VIEW_FORMATS[format],
                        adapter_name=RENDERER_NAME, adapter_version=RENDERER_VERSION,
                        warnings=layout_warnings + list(figs.warnings))


def small_text(items: list, profile: Any) -> list[RenderWarning]:
    """``legibility_text_too_small`` for view text (own display list; scaled
    flows count at their scale) below the profile's minimum."""
    found: list[tuple[float, str]] = []

    def walk(its: list, k: float) -> None:
        for it in its:
            if isinstance(it, Group):
                walk(it.items, k * it.scale)
            elif isinstance(it, Label) and it.text.strip() and it.size * k < \
                    profile.min_text_px - 1e-9:
                found.append((it.size * k, it.text))

    walk(items, 1.0)
    if not found:
        return []
    found.sort()
    return [RenderWarning("legibility_text_too_small", (
        f"{len(found)} text(s) of the view are below {profile.min_text_px:g} CSS px "
        f"({profile.name}); smallest: "
        + ", ".join(f"{t[:40]!r} at {s:.1f} px" for s, t in found[:3]) + "."), "")]
