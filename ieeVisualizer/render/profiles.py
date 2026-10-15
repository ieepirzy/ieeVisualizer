"""Render profiles: where an output will be read, and what that demands.

A :class:`RenderProfile` names a delivery target by the narrowest width (CSS
px) its content is shown at, the raster width of a full-width image, and the
smallest text size readable there. ``RenderRequest(profile=...)`` makes the
matplotlib adapter enforce the minimum (fonts are raised to it), adapt the
layout where that is safe on a narrow profile, and audit the drawn figure:
text smaller than the minimum, text overlapping text and text past the
image edge become ``legibility_*`` warnings on the result, never silence.
:func:`check_legible` turns them into an error for a pipeline that must not
send an illegible image. Design notes: ``docs/MOBILE_AND_EMAIL.md``.

Nothing here imports matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..spec import ValidationIssue
from .base import RenderError, RenderRequest

#: Warning codes of the legibility audit (see :func:`check_legible`).
LEGIBILITY_CODES = ("legibility_text_too_small", "legibility_text_overlap",
                    "legibility_text_clipped", "too_many_categories_for_profile")


@dataclass(frozen=True)
class RenderProfile:
    """A delivery target.

    ``css_width`` is the narrowest content width (CSS px) a full-width image
    is shown at; ``pixel_width`` the raster width of such an image (its
    ratio is the pixel density the image carries). ``min_text_px`` is the
    smallest font size (em, CSS px) allowed in images, ``min_html_text_px``
    in HTML text. ``narrow`` switches on the narrow-screen layout rules;
    ``aspect`` is the default figure height / width.
    """

    name: str
    css_width: float
    pixel_width: int
    min_text_px: float
    min_html_text_px: float
    narrow: bool
    aspect: float
    description: str = ""

    @property
    def scale(self) -> float:
        """Image pixels per CSS px at :attr:`css_width`."""
        return self.pixel_width / self.css_width

    def min_text_pt(self, figure_width_in: float, display_width: float | None = None) -> float:
        """Smallest font size (pt) for a figure ``figure_width_in`` inches
        wide shown ``display_width`` CSS px wide (default :attr:`css_width`):
        s pt is s/72 in on the figure, i.e. s/72 * display/width CSS px."""
        return self.min_text_px * 72.0 * figure_width_in / (display_width or self.css_width)

    def figure_request(self, format: str = "png", *, aspect: float | None = None,
                       display_width: float | None = None, theme: str | None = None) -> Any:
        """A ``RenderRequest`` for a figure shown ``display_width`` CSS px
        wide (default: the full content width). At ``dpi = 72 * scale`` one
        point is one CSS px, so theme font sizes read as CSS px."""
        w = float(display_width or self.css_width)
        px = round(w * self.scale)
        return RenderRequest(format=format, width=px, height=round(px * (aspect or self.aspect)),
                             units="px", dpi=72.0 * self.scale, theme=theme,
                             profile=self.name,
                             display_width=None if display_width is None else w)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "css_width": self.css_width,
                "pixel_width": self.pixel_width, "min_text_px": self.min_text_px,
                "min_html_text_px": self.min_html_text_px, "narrow": self.narrow,
                "aspect": self.aspect, "description": self.description}


_PROFILES = {p.name: p for p in (
    RenderProfile(
        "email-phone", 320, 1200, 12.0, 13.0, True, 0.8,
        "Phone email (viewports from 360 CSS px): content at least 320 px wide after the "
        "email's 12 px gutters and up to 16 px of client margin. 1200 px images stay sharp "
        "at 3x on phones and at 2x when a 600 px tablet column shows them; the default for "
        "email, since one image must serve both."),
    RenderProfile(
        "email-tablet", 600, 1200, 12.0, 13.0, False, 0.6,
        "Tablet or desktop email: the usual 600 px email column at 2x. Text is too small "
        "if the same image is read on a phone (shown about 320 px wide)."),
    RenderProfile(
        "desktop", 960, 1920, 12.0, 13.0, False, 0.6,
        "Browser or report page 960 CSS px wide at 2x."),
)}


def get_profile(name: str) -> RenderProfile:
    try:
        return _PROFILES[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown render profile {name!r}; use one of "
                         f"{', '.join(_PROFILES)}.") from None


def list_profiles() -> list[RenderProfile]:
    return list(_PROFILES.values())


def check_legible(result: Any) -> None:
    """Raise :class:`~ieeVisualizer.render.LegibilityError` if ``result``
    (a ``RenderResult``) carries any legibility warning."""
    bad = [w for w in result.warnings if w.code in LEGIBILITY_CODES]
    if bad:
        raise LegibilityError([ValidationIssue(w.path, w.code, w.message) for w in bad])


class LegibilityError(RenderError):
    """A render made for a profile is not legible there (see :func:`check_legible`)."""
