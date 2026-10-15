"""Theme data, registry and accessibility refusal."""

import dataclasses
import json

import pytest

from ieeVisualizer import themes
from ieeVisualizer.themes import RoleStyle, Theme, ThemeError, ThemeRegistry, check_theme


def base(**kw):
    kw.setdefault("name", "t")
    kw.setdefault("version", "1.0.0")
    kw.setdefault("background", "#ffffff")
    kw.setdefault("foreground", "#111111")
    kw.setdefault("palette", ("#007c47", "#2f4380", "#9262b6"))
    return Theme(**kw)


def codes(issues, severity="error"):
    return sorted({(i.path, i.code) for i in issues if i.severity == severity})


def test_a_good_theme_passes_and_registers():
    reg = ThemeRegistry()
    t = reg.register(base())
    assert check_theme(t) == []
    assert reg.resolve("t") is t and reg.resolve("t@1.0.0") is t


@pytest.mark.parametrize("kw, expected", [
    ({"foreground": "#aaaaaa"}, ("/foreground", "text_contrast")),
    ({"muted": "#999999"}, ("/muted", "text_contrast")),
    ({"palette": ("#007c47", "#ffd400")}, ("/palette/1", "mark_contrast")),
    ({"palette": ("#2f4380", "#30447f")}, ("/palette/1", "palette_indistinct")),
    # the admin tokens --indigo and --cyan on the admin ground: distinct for normal
    # vision, the same colour under protanopia/deuteranopia
    ({"background": "#08090a", "foreground": "#d9d6cf", "palette": ("#9db8ff", "#d7a6ff")},
     ("/palette/1", "palette_cvd_indistinct")),
    ({"roles": RoleStyle(reference_color="#545454", reference_fill_alpha=0.3)},
     ("/roles/reference_fill_alpha", "mark_contrast")),
    ({"roles": RoleStyle(forecast_fill_alpha=0.3)}, ("/roles/forecast_fill_alpha",
                                                      "mark_contrast")),
    ({"diverging": ("#d62728", "#eeeeee", "#2ca02c")}, ("/diverging",
                                                        "diverging_cvd_indistinct")),
])
def test_failing_theme_is_refused_with_structured_issues(kw, expected):
    with pytest.raises(ThemeError) as e:
        ThemeRegistry().register(base(**kw))
    assert expected in codes(e.value.issues)
    assert all(i.message for i in e.value.issues)


@pytest.mark.parametrize("kw, expected", [
    ({"roles": RoleStyle(forecast_linestyle="solid")},
     ("/roles/forecast_linestyle", "forecast_not_distinguishable")),
    ({"palette": ("#007c47", "nope")}, ("/palette/1", "invalid_theme")),
    ({"signal": "#007c47"}, ("/signal", "signal_in_palette")),
    ({"name": "Bad Name"}, ("/name", "invalid_theme")),
    ({"version": "1.0"}, ("/version", "invalid_theme")),
])
def test_malformed_theme_is_refused(kw, expected):
    with pytest.raises(ThemeError) as e:
        ThemeRegistry().register(base(**kw))
    assert expected in codes(e.value.issues)


def test_warnings_do_not_refuse_and_are_kept():
    reg = ThemeRegistry()
    t = reg.register(base(sequential=("#fafafa", "#2f4380")))
    assert codes(reg.findings("t"), "warning") == [("/sequential/0", "colormap_near_background")]
    assert codes(check_theme(themes.get("default")), "info") == [("/palette",
                                                                   "palette_unchecked")]
    assert t.ref == "t@1.0.0"


def test_versions_duplicates_and_unknown_names():
    reg = ThemeRegistry()
    reg.register(base())
    reg.register(base(version="1.10.0"))
    reg.register(base(version="1.2.0"))
    assert reg.resolve("t").version == "1.10.0"  # numeric, not string, order
    with pytest.raises(ThemeError) as e:
        reg.register(base())
    assert e.value.issues[0].code == "duplicate_theme"
    with pytest.raises(ThemeError) as e:
        reg.resolve("t@9.9.9")
    assert e.value.issues[0].code == "unknown_theme_version"
    with pytest.raises(ThemeError) as e:
        themes.default_registry.resolve("muutto365-report-lite")
    assert "Did you mean 'muutto365-report-light'?" in e.value.issues[0].message


def test_theme_is_json_data_with_a_content_hash():
    for entry in themes.list_themes():
        t = themes.get(entry["name"])
        text = t.to_json()
        again = Theme.from_json(text)
        assert again == t and again.sha256 == t.sha256
        assert json.loads(text) == t.to_dict()
    t = themes.get("muutto365-report-light")
    changed = dataclasses.replace(t, palette=("#007c47", "#2f4380"))
    assert changed.sha256 != t.sha256
    assert t.record() == {"name": t.name, "version": "1.0.0", "sha256": t.sha256}
    with pytest.raises(ThemeError) as e:
        Theme.from_dict(dict(t.to_dict(), colour="#fff"))
    assert e.value.issues[0].path == "/colour"


def test_target_requests():
    t = themes.get("muutto365-report-light")
    req = t.request("email", "png")
    assert req.size_px == (1200, 750) and req.theme == "muutto365-report-light@1.0.0"
    assert t.request("slide").size_px == (1920, 1080)
    with pytest.raises(ThemeError):
        t.request("poster")


def test_importing_themes_does_not_import_matplotlib():
    import subprocess
    import sys

    code = ("import sys, ieeVisualizer.themes as t; t.get('muutto365-admin-dark'); "
            "print('matplotlib' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "False"
