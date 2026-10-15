"""Built-in themes.

``default``, ``minimal`` and ``presentation`` reproduce the matplotlib
adapter's earlier theme presets exactly (they declare only what those
presets changed). The two Muutto365 themes are derived from real design
tokens; every colour's origin is in its ``provenance``:

* ``muutto365-admin-dark``: movingfirm-admin ``frontend/src/styles/tokens.css``
  (the admin dashboard's "night-shift" tokens).
* ``muutto365-report-light``: no brand guideline for external or light
  use exists in the movingfirm-* repositories. The ground, ink, hairline and
  accent come from the public landing page's tokens (movingfirm-admin
  ``tests/fixtures/interface/home.css``, a copy of the CSS production
  served); the other series colours are the admin token hues darkened until
  they pass contrast on white. Its ``pending`` slots await an official brand
  palette.

The derived values are fixed here as data and recomputed by the test suite
(``tests/themes/test_builtin_themes.py``) from the cited tokens.
"""

from __future__ import annotations

from .locale import FI_FI
from .theme import FigureTarget, RoleStyle, Theme, Typography

ADMIN_TOKENS = "movingfirm-admin/frontend/src/styles/tokens.css"
SITE_TOKENS = "movingfirm-admin/tests/fixtures/interface/home.css"

TARGETS = {
    "email": FigureTarget(6.0, 3.75, 200.0, "1200 x 750 px; shown about 600 px wide in mail"),
    "slide": FigureTarget(8.0, 4.5, 240.0, "1920 x 1080 px (16:9)"),
}

DEFAULT = Theme(
    name="default", version="1.0.0", title="Default",
    description="The matplotlib adapter's plain look (matplotlib 'default' style). Declares "
                "nothing, so renders are byte-identical to those made before themes existed.",
    provenance={"source": "matplotlib default style of the recorded matplotlib version"},
)

MINIMAL = Theme(
    name="minimal", version="1.0.0", title="Minimal",
    description="Default colours without top/right spines, with a light grid.",
    grid_show=True, grid_alpha=0.3, grid_width=0.6, spines=("left", "bottom"),
    provenance={"source": "the adapter's earlier 'minimal' preset"},
)

PRESENTATION = Theme(
    name="presentation", version="1.0.0", title="Presentation",
    description="Larger type and thicker lines for slides; default colours.",
    typography=Typography(size=13.0, title=16.0, label=14.0), line_width=2.2,
    spines=("left", "bottom"),
    provenance={"source": "the adapter's earlier 'presentation' preset"},
)

_FONT_NOTE = ("Brand UI fonts (admin: Geist / IBM Plex Sans; site: Public Sans) are not "
              "bundled; DejaVu Sans ships with matplotlib, so a render is byte-identical on "
              "every machine.")

ADMIN_DARK = Theme(
    name="muutto365-admin-dark", version="1.0.0", title="Muutto365 admin (dark)",
    description="Dark theme matching the Muutto365 admin dashboard tokens, for internal "
                "dashboards and slides.",
    palette=("#9db8ff", "#6fd3a0", "#9d6ec2"),
    sequential=("#3b528b", "#2f6b8e", "#26828e", "#1f988b", "#28ae80", "#4ec36b", "#84d44b",
                "#c2df23", "#fde725"),
    diverging=("#6fd3a0", "#5f5d58", "#9d6ec2"),
    background="#08090a", foreground="#d9d6cf", muted="#95928a", grid="#1f1f1f",
    signal="#ffb547",
    semantic={"positive": "#6fd3a0", "negative": "#ff6e5e", "attention": "#ffb547"},
    typography=Typography(family=("DejaVu Sans",), caption=8.0),
    line_width=1.8, grid_show=True, grid_alpha=1.0, grid_width=0.6, grid_below=True,
    spines=("left", "bottom"),
    roles=RoleStyle(forecast_linestyle="dashed", forecast_hatch="///",
                    forecast_fill_alpha=0.75, reference_color="#95928a", reference_alpha=0.8,
                    reference_fill_alpha=0.7, reference_linewidth=1.2),
    locale=FI_FI, targets=dict(TARGETS),
    provenance={
        "source": ADMIN_TOKENS,
        "background": "--bg #08090a",
        "foreground": "--text #d9d6cf",
        "muted": "--text2 #95928a (--text3 #5f5d58 is 3.0:1 on --bg: too faint for text)",
        "grid": "--border2 rgba(236,232,224,0.1) composited on --bg",
        "palette": ["--indigo #9db8ff (info / primary series in the admin charts)",
                    "--green #6fd3a0 (admin LineCompare primary)",
                    "--cyan #d7a6ff hue at OKLCH lightness 0.62: the token itself is "
                    "indistinguishable from --indigo under protanopia/deuteranopia "
                    "(CIEDE2000 2.1 / 1.8)"],
        "signal": "--orange #ffb547: 'needs a person'; reserved, never a series colour",
        "semantic": "--green healthy, --red broken, --orange needs a person",
        "sequential": "matplotlib viridis sampled on [0.25, 1] (low end visible on --bg)",
        "diverging": "--green / --text3 / derived lilac",
        "fonts": _FONT_NOTE,
        "locale": "fi-FI",
    },
)

REPORT_LIGHT = Theme(
    name="muutto365-report-light", version="1.0.0", title="Muutto365 report (light)",
    description="Neutral, accessible light theme for email and print. Awaits an official "
                "brand palette: the 'pending' slots are placeholders.",
    palette=("#007c47", "#2f4380", "#9262b6"),
    sequential=("#440154", "#482576", "#404588", "#34618d", "#297a8e", "#20938c", "#25ac82",
                "#4cc26c", "#86d549"),
    diverging=("#007c47", "#cecece", "#9262b6"),
    background="#ffffff", foreground="#0a0a0a", muted="#545454", grid="#e2e2e2",
    signal="#cc8600",
    semantic={"positive": "#148759", "negative": "#d24539", "attention": "#cc8600"},
    typography=Typography(family=("DejaVu Sans",), caption=8.0),
    line_width=1.8, grid_show=True, grid_alpha=1.0, grid_width=0.6, grid_below=True,
    spines=("left", "bottom"),
    roles=RoleStyle(forecast_linestyle="dashed", forecast_hatch="///",
                    forecast_fill_alpha=0.85, forecast_line_alpha=0.85,
                    reference_color="#545454", reference_alpha=0.8,
                    reference_fill_alpha=0.7, reference_linewidth=1.2),
    locale=FI_FI, targets=dict(TARGETS),
    pending=("palette", "signal", "semantic", "typography.family"),
    provenance={
        "source": f"{SITE_TOKENS} (public site) and {ADMIN_TOKENS}; no brand guideline for "
                  "external/light use exists in the movingfirm-* repositories",
        "background": "site --paper #ffffff",
        "foreground": "site --ink #0a0a0a",
        "muted": "--ink at 70 % on --paper (site --text-dim-ink, ink at 55 %, is 4.4:1: "
                 "below 4.5:1 for text)",
        "grid": "site --line rgba(10,10,10,.12) composited on --paper",
        "palette": ["site --mint-deep oklch(0.50 0.15 162) (link/emphasis colour on white)",
                    "admin --indigo hue at OKLCH lightness 0.40 (dark enough to stay apart "
                    "from the purple under protan/deutan simulation)",
                    "admin --cyan hue darkened to 4.5:1 on white"],
        "signal": "admin --orange hue darkened to 3:1 on white; reserved",
        "semantic": "admin --green / --red hues darkened to 4.5:1, signal as attention",
        "sequential": "matplotlib viridis sampled on [0, 0.82] (high end visible on white)",
        "diverging": "palette ends with --ink at 20 % as the neutral midpoint",
        "roles": "forecast lines at 85 % opacity: at the renderer's default 75 % the "
                 "purple series colour is 2.92:1 on white, below 3:1 for graphics",
        "fonts": _FONT_NOTE,
        "locale": "fi-FI",
        "pending": "replace the pending slots with the official brand palette when one "
                   "exists; publish it as a new theme version",
    },
)

BUILTIN = (DEFAULT, MINIMAL, PRESENTATION, ADMIN_DARK, REPORT_LIGHT)
