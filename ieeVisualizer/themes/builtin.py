"""Built-in themes.

``default``, ``minimal`` and ``presentation`` reproduce the matplotlib
adapter's earlier theme presets exactly (they declare only what those
presets changed). ``dashboard-dark`` and ``report-light`` are neutral,
organisation-free themes for internal dashboards/slides and for email/print
reports. Their series colours are taken from published colour-vision-safe
qualitative palettes (Paul Tol's "vibrant", "muted" and "light" schemes),
their colour maps from matplotlib's ``cividis``, and their greys are plain
neutrals chosen for contrast; every value's origin is in its ``provenance``.
``tests/themes/test_builtin_themes.py`` checks the properties they promise
(contrast, distinctness under simulated colour-vision deficiency, forecast
lines as drawn), not the hex values.

Organisation-specific themes do not belong in this library: publish them
from their own package and add them with :func:`ieeVisualizer.themes.register`.
"""

from __future__ import annotations

from .locale import FI_FI
from .theme import FigureTarget, RoleStyle, Theme, Typography

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

_FONT_NOTE = ("DejaVu Sans ships with matplotlib, so a render is byte-identical on every "
              "machine; a host may list other families, the first installed one is used.")
_TOL = "Paul Tol, 'Colour Schemes' (SRON/EPS/TN/09-002, 2021)"

DASHBOARD_DARK = Theme(
    name="dashboard-dark", version="1.0.0", title="Dashboard (dark)",
    description="Neutral dark theme for internal dashboards and slides.",
    palette=("#33bbee", "#ee3377", "#009988", "#ffaabb"),
    sequential=("#4f576c", "#646770", "#777776", "#8c8878", "#a19975", "#b8aa6e", "#d0be62",
                "#e8d24f", "#fee838"),
    diverging=("#33bbee", "#6b6e76", "#ee3377"),
    background="#16181d", foreground="#e8e8e8", muted="#a3a3a3", grid="#2b2e35",
    signal="#ee7733",
    semantic={"positive": "#44bb99", "negative": "#ee6677", "attention": "#ee7733"},
    typography=Typography(family=("DejaVu Sans",), caption=8.0),
    line_width=1.8, grid_show=True, grid_alpha=1.0, grid_width=0.6, grid_below=True,
    spines=("left", "bottom"),
    roles=RoleStyle(forecast_linestyle="dashed", forecast_hatch="///",
                    forecast_fill_alpha=0.75, reference_color="#a3a3a3", reference_alpha=0.8,
                    reference_fill_alpha=0.7, reference_linewidth=1.2),
    locale=FI_FI, targets=dict(TARGETS),
    provenance={
        "source": "designed for ieeVisualizer; series colours from " + _TOL,
        "background": "neutral blue-grey, dark enough for 14:1 text",
        "foreground": "neutral light grey (14.5:1)",
        "muted": "neutral grey (7.0:1)",
        "grid": "background lifted slightly (1.3:1): visible, never competing with data",
        "palette": ["Tol vibrant cyan #33bbee", "Tol vibrant magenta #ee3377",
                    "Tol vibrant teal #009988", "Tol light pink #ffaabb"],
        "signal": "Tol vibrant orange #ee7733; reserved, never a series colour",
        "semantic": "Tol light mint (positive), Tol bright red (negative), signal as attention",
        "sequential": "matplotlib cividis sampled on [0.3, 1] (low end visible on the ground)",
        "diverging": "palette cyan / neutral grey / palette magenta",
        "fonts": _FONT_NOTE,
        "locale": "fi-FI",
    },
)

REPORT_LIGHT = Theme(
    name="report-light", version="1.0.0", title="Report (light)",
    description="Neutral, accessible light theme for email and print.",
    palette=("#0077bb", "#117733", "#aa3377", "#332288"),
    sequential=("#00224e", "#083370", "#35456c", "#4f576c", "#666970", "#7d7c78", "#948e77",
                "#aea371", "#c8b866"),
    diverging=("#0077bb", "#d0d0d0", "#cc3311"),
    background="#ffffff", foreground="#1a1a1a", muted="#616161", grid="#dcdcdc",
    signal="#a65e00",
    semantic={"positive": "#117733", "negative": "#cc3311", "attention": "#a65e00"},
    typography=Typography(family=("DejaVu Sans",), caption=8.0),
    line_width=1.8, grid_show=True, grid_alpha=1.0, grid_width=0.6, grid_below=True,
    spines=("left", "bottom"),
    roles=RoleStyle(forecast_linestyle="dashed", forecast_hatch="///",
                    forecast_fill_alpha=0.75, reference_color="#616161", reference_alpha=0.8,
                    reference_fill_alpha=0.7, reference_linewidth=1.2),
    locale=FI_FI, targets=dict(TARGETS),
    provenance={
        "source": "designed for ieeVisualizer; series colours from " + _TOL,
        "background": "white",
        "foreground": "near-black neutral (17.4:1)",
        "muted": "neutral grey (6.2:1)",
        "grid": "light neutral grey (1.4:1)",
        "palette": ["Tol vibrant blue #0077bb", "Tol muted green #117733",
                    "Tol bright purple #aa3377", "Tol muted indigo #332288"],
        "signal": "dark amber, 5.0:1 on white; reserved, never a series colour",
        "semantic": "palette green (positive), Tol vibrant red (negative), signal as attention",
        "sequential": "matplotlib cividis sampled on [0, 0.8] (high end visible on white)",
        "diverging": "palette blue / light neutral grey / Tol vibrant red",
        "fonts": _FONT_NOTE,
        "locale": "fi-FI",
    },
)

BUILTIN = (DEFAULT, MINIMAL, PRESENTATION, DASHBOARD_DARK, REPORT_LIGHT)
