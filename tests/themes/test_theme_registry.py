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
    # at the default forecast line opacity (0.75) #9262b6 is 2.92:1 on white
    kw.setdefault("roles", RoleStyle(forecast_line_alpha=0.85))
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
    # a forecast line is translucent even when the theme says nothing about it
    ({"roles": RoleStyle()}, ("/roles/forecast_line_alpha", "mark_contrast")),
    ({"roles": RoleStyle(forecast_line_alpha=0.5)}, ("/roles/forecast_line_alpha",
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
        themes.default_registry.resolve("report-lite")
    assert "Did you mean 'report-light'?" in e.value.issues[0].message


def test_theme_is_json_data_with_a_content_hash():
    for entry in themes.list_themes():
        t = themes.get(entry["name"])
        text = t.to_json()
        again = Theme.from_json(text)
        assert again == t and again.sha256 == t.sha256
        assert json.loads(text) == t.to_dict()
    t = themes.get("report-light")
    changed = dataclasses.replace(t, palette=("#007c47", "#2f4380"))
    assert changed.sha256 != t.sha256
    assert t.record() == {"name": t.name, "version": "1.0.0", "sha256": t.sha256}
    with pytest.raises(ThemeError) as e:
        Theme.from_dict(dict(t.to_dict(), colour="#fff"))
    assert e.value.issues[0].path == "/colour"


def test_target_requests():
    t = themes.get("report-light")
    req = t.request("email", "png")
    assert req.size_px == (1200, 750) and req.theme == "report-light@1.0.0"
    assert t.request("slide").size_px == (1920, 1080)
    with pytest.raises(ThemeError):
        t.request("poster")


def test_importing_themes_does_not_import_matplotlib():
    import subprocess
    import sys

    code = ("import sys, ieeVisualizer.themes as t; t.get('admin-dark'); "
            "print('matplotlib' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "False"


def test_mutating_the_callers_objects_cannot_change_a_registered_theme():
    palette = ["#007c47", "#2f4380"]
    semantic = {"negative": "#d24539"}
    provenance = {"palette": ["a", "b"], "nested": {"k": "v"}}
    targets = {"email": themes.FigureTarget(6.0, 3.75, 100)}
    reg = ThemeRegistry()
    t = reg.register(base(name="mut", palette=palette, semantic=semantic,
                          provenance=provenance, targets=targets))
    before = t.sha256
    palette[1] = "#00804a"  # near-identical green: would fail palette_indistinct
    semantic["negative"] = "#fefefe"
    provenance["palette"].append("c")
    provenance["nested"]["k"] = "changed"
    targets["slide"] = targets["email"]
    resolved = reg.resolve("mut@1.0.0")
    assert resolved.sha256 == before and t.sha256 == before
    assert resolved.palette == ("#007c47", "#2f4380")
    assert check_theme(resolved) == []
    # nor can the returned theme be mutated in place
    for attempt in (lambda: t.semantic.__setitem__("negative", "#fefefe"),
                    lambda: t.targets.update(slide=t.targets["email"]),
                    lambda: t.provenance["nested"].__setitem__("k", "x"),
                    lambda: t.semantic.pop("negative"),
                    lambda: t.palette.append("#000000")):
        with pytest.raises((TypeError, AttributeError)):
            attempt()
    assert reg.resolve("mut@1.0.0").sha256 == before
    assert isinstance(t.provenance["palette"], tuple)
    assert json.loads(t.to_json()) == t.to_dict()  # still plain JSON data


def test_register_stores_its_own_frozen_copy():
    # even a theme whose fields were forced past __post_init__ is re-frozen
    t = base(name="forced")
    palette = ["#007c47", "#2f4380"]
    object.__setattr__(t, "palette", palette)
    stored = ThemeRegistry().register(t)
    palette[1] = "#00804a"
    assert stored.palette == ("#007c47", "#2f4380")


def test_mutating_the_callers_palette_does_not_change_rendered_output():
    from ieeVisualizer.render import RenderRequest, render
    from ieeVisualizer.spec import DataSnapshot, FigureSpec, LineMark

    palette = ["#007c47", "#2f4380"]
    themes.register(base(name="mut-render", palette=palette))
    data = DataSnapshot.from_columns({"t": [0, 1], "a": [1.0, 2.0], "b": [2.0, 1.0]},
                                     units={"t": "d"})
    spec = FigureSpec(datasets={"d": data}, theme="mut-render@1.0.0", layers=[
        LineMark(data="d", encoding={"x": "t", "y": c}, name=c) for c in "ab"])
    req = RenderRequest(format="svg", width=3, height=2)
    first = render(spec, req)
    palette[:] = ["#ff0000", "#00ff00"]
    again = render(spec, req)
    assert again.content == first.content and again.theme == first.theme


@pytest.mark.parametrize("bad, path", [
    ({"semantic": ["#000"]}, "/semantic"),
    ({"semantic": {"negative": 5}}, "/semantic/negative"),
    ({"targets": ["email"]}, "/targets"),
    ({"targets": {"email": 1}}, "/targets/email"),
    ({"typography": ["DejaVu Sans"]}, "/typography"),
    ({"roles": "dashed"}, "/roles"),
    ({"locale": "fi-FI"}, "/locale"),
    ({"palette": "#007c47"}, "/palette"),
    ({"provenance": ["x"]}, "/provenance"),
    ({"pending": None}, "/pending"),
])
def test_malformed_theme_json_is_a_theme_error_not_a_crash(bad, path):
    with pytest.raises(ThemeError) as e:
        Theme.from_dict(dict({"name": "x", "version": "1.0.0"}, **bad))
    assert (path, "invalid_theme") in codes(e.value.issues)


def test_malformed_directly_built_theme_is_refused_with_issues():
    for kw, path in (({"semantic": ["#000"]}, "/semantic"),
                     ({"typography": ["x"]}, "/typography"),
                     ({"targets": {"email": 1}}, "/targets/email")):
        with pytest.raises(ThemeError) as e:
            ThemeRegistry().register(base(**kw))
        assert (path, "invalid_theme") in codes(e.value.issues)
