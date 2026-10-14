"""Roles, warnings, refusals, references and capability selection."""

import pytest
from render_examples import (
    function_spec,
    latency_snapshot,
    one_layer,
    quiver_spec,
    ref_spec,
    revenue_spec,
)

from ieeVisualizer.render import (
    AdapterRegistry,
    Capabilities,
    CapabilityError,
    MatplotlibAdapter,
    RenderError,
    RenderRequest,
    RenderResult,
    UnresolvedDatasetError,
    build_figure,
    default_registry,
    render,
    requirements_for,
    select_adapter,
)
from ieeVisualizer.spec import (
    BarMark,
    FigureSpec,
    FunctionMark,
    LineMark,
    ScatterMark,
    SpecValidationError,
)

# ----------------------------------------------------------------- roles


def test_forecast_is_dashed_and_labelled_observed_is_solid():
    built = build_figure(revenue_spec())
    (obs,) = built.layer_artists[0]
    (fc,) = built.layer_artists[1]
    assert obs.get_linestyle() == "-"
    assert fc.get_linestyle() == "--"
    assert fc.get_label() == "Forecast"  # already says forecast; not doubled
    assert fc.get_color() == obs.get_color()  # same series, continued


def test_forecast_label_says_forecast_when_name_does_not():
    spec = revenue_spec()
    spec.layers[1].name = "Model B"
    built = build_figure(spec)
    assert built.layer_artists[1][0].get_label() == "Model B (forecast)"
    spec.layers[1].name = None
    assert build_figure(spec).layer_artists[1][0].get_label() == "revenue (forecast)"


def test_forecast_cannot_be_styled_solid():
    built = build_figure(revenue_spec(linestyle="solid"))
    assert built.layer_artists[1][0].get_linestyle() == "--"
    w = [w for w in built.warnings if w.code == "forecast_style_overridden"]
    assert w and w[0].path == "/layers/1/style/linestyle"


def test_forecast_scatter_is_hollow_and_bar_hatched():
    spec = one_layer(ScatterMark(encoding={"x": "a", "y": "b"}, role="forecast"),
                     {"a": [1.0, 2.0], "b": [3.0, 4.0]})
    coll = build_figure(spec).layer_artists[0][0]
    assert coll.get_facecolors().size == 0 or coll.get_facecolors()[0][3] == 0
    spec = one_layer(BarMark(encoding={"x": "a", "y": "b"}, role="forecast"),
                     {"a": ["p", "q"], "b": [3.0, 4.0]})
    bars = build_figure(spec).layer_artists[0][0]
    assert all(p.get_hatch() for p in bars)


def test_reference_role_is_lighter():
    built = build_figure(function_spec(role="reference"))
    (line,) = built.layer_artists[0]
    assert line.get_alpha() is not None and line.get_alpha() < 1
    assert line.get_linewidth() <= 1.0


# ----------------------------------------------------------------- warnings


def test_unknown_style_key_is_a_warning_not_silent():
    spec = function_spec(style={"glow": 3, "color": "tab:red"})
    built = build_figure(spec)
    (w,) = [w for w in built.warnings if w.code == "unknown_style_key"]
    assert w.path == "/layers/0/style/glow" and "glow" in w.message
    assert built.layer_artists[0][0].get_color() == "tab:red"  # known keys still apply
    result = render(spec, RenderRequest(format="svg"))
    assert "unknown_style_key" in [w.code for w in result.warnings]


def test_invalid_and_inapplicable_style_values_warn():
    spec = quiver_spec()
    spec.layers[0].style = {"color": "not-a-colour", "linestyle": "dashed"}
    codes = {(w.code, w.path) for w in build_figure(spec).warnings}
    assert ("invalid_style_value", "/layers/0/style/color") in codes
    assert ("style_key_ignored", "/layers/0/style/linestyle") in codes


def test_unknown_theme_warns_and_request_theme_overrides():
    spec = revenue_spec()
    spec.theme = "corporate"
    r = render(spec, RenderRequest(format="svg"))
    assert [w.path for w in r.warnings if w.code == "unknown_theme"] == ["/theme"]
    r = render(spec, RenderRequest(format="svg", theme="minimal"))
    assert "unknown_theme" not in [w.code for w in r.warnings]


# ----------------------------------------------------------------- refusals


def test_invalid_spec_is_refused_with_issues():
    spec = one_layer(LineMark(encoding={"x": "a", "y": "nope"}), {"a": [1.0, 2.0]})
    with pytest.raises(SpecValidationError) as e:
        render(spec, RenderRequest(format="svg"))
    assert [i.code for i in e.value.issues] == ["unknown_column"]
    with pytest.raises(SpecValidationError):
        MatplotlibAdapter().render(spec, RenderRequest(format="png"))
    with pytest.raises(SpecValidationError):
        build_figure(FigureSpec())  # no layers


def test_reference_without_resolver_is_refused():
    with pytest.raises(UnresolvedDatasetError) as e:
        render(ref_spec(), RenderRequest(format="svg"))
    (i,) = e.value.issues
    assert i.code == "unresolved_dataset" and i.path == "/datasets/lat"
    assert "resolver" in i.message


def test_reference_resolves_and_adopts_declared_unit():
    seen = []

    def resolver(ref):
        seen.append(ref.id)
        return latency_snapshot()

    built = build_figure(ref_spec(), resolver=resolver)
    assert seen == ["bookings.latency"]
    assert built.axes.get_xlabel() == "Latency (s)"
    assert sum(p.get_height() for p in built.layer_artists[0][0]) == 4  # null not counted


def test_resolved_data_must_match_declared_schema():
    with pytest.raises(UnresolvedDatasetError) as e:
        build_figure(ref_spec(), resolver=lambda ref: latency_snapshot(unit="ms"))
    assert e.value.issues[0].code == "resolved_schema_mismatch"
    with pytest.raises(UnresolvedDatasetError) as e:
        build_figure(ref_spec(), resolver=lambda ref: 42)
    assert e.value.issues[0].code == "resolver_failed"

    def boom(ref):
        raise ConnectionError("db down")

    with pytest.raises(UnresolvedDatasetError) as e:
        build_figure(ref_spec(), resolver=boom)
    assert "db down" in e.value.issues[0].message


def test_resolved_data_that_is_empty_is_refused():
    with pytest.raises(SpecValidationError) as e:
        build_figure(ref_spec(), resolver=lambda ref: latency_snapshot(values=()))
    assert "empty_dataset" in [i.code for i in e.value.issues]


@pytest.mark.parametrize("expr", [
    "__import__('os').system('true')",
    "x.__class__",
    "eval(x)",
    "Symbol('y')",
    "lambda: 1",
])
def test_unsafe_or_unknown_expressions_are_refused(expr):
    spec = FigureSpec(layers=[FunctionMark(expression=expr, domain={"x": [0.0, 1.0]})])
    with pytest.raises(RenderError) as e:
        build_figure(spec)
    assert e.value.issues[0].code in ("invalid_expression", "unknown_symbol")


def test_expression_with_exponent_literal_and_constants_is_fine():
    spec = FigureSpec(layers=[FunctionMark(expression="1e-3*x + pi*sin(2.5*x)^2",
                                           domain={"x": [0.0, 1.0]}, samples=3)])
    build_figure(spec)


# ----------------------------------------------------------------- capabilities


class _NarrowAdapter:
    name = "narrow"
    version = "0.0.1"
    capabilities = Capabilities(
        coordinate_systems=frozenset({"cartesian2d"}),
        mark_types=frozenset({"line"}),
        output_formats=frozenset({"png"}),
        max_points=3,
    )

    def render(self, spec, request, *, resolver=None):  # pragma: no cover - never chosen
        return RenderResult(b"", request.format, request.media_type, self.name, self.version)


def test_capability_mismatch_lists_every_unmet_requirement():
    reg = AdapterRegistry()
    reg.register(_NarrowAdapter())
    spec = quiver_spec()
    with pytest.raises(CapabilityError) as e:
        select_adapter(spec, RenderRequest(format="svg"), reg)
    unmet = e.value.unmet["narrow"]
    joined = " | ".join(unmet)
    assert "mark types quiver" in joined
    assert "output format 'svg'" in joined
    assert "vector export" in joined
    assert "4 data points (max 3" in joined
    msg = str(e.value)
    assert "narrow cannot provide" in msg and "The spec needs:" in msg
    assert "mark types quiver" in e.value.requirements[1]


def test_selection_falls_through_to_a_capable_adapter():
    reg = AdapterRegistry()
    reg.register(_NarrowAdapter())
    reg.register(MatplotlibAdapter())
    assert select_adapter(quiver_spec(), RenderRequest(format="svg"), reg).name == "matplotlib"
    with pytest.raises(CapabilityError):  # pinned to the narrow one: no fallback
        select_adapter(quiver_spec(), RenderRequest(format="svg", adapter="narrow"), reg)


def test_default_registry_refuses_html_clearly():
    assert default_registry.names() == ["matplotlib"]
    with pytest.raises(CapabilityError) as e:
        render(revenue_spec(), RenderRequest(format="html"))
    assert "output format 'html'" in str(e.value)
    with pytest.raises(CapabilityError):
        MatplotlibAdapter().render(revenue_spec(), RenderRequest(format="html"))


def test_requirements_reflect_the_spec():
    from render_examples import surface_spec

    r = requirements_for(surface_spec(), RenderRequest(format="pdf"))
    assert r.three_d and r.vector_export and r.points == 400
    assert r.mark_types == {"function"}
    r = requirements_for(ref_spec(), RenderRequest())
    assert r.unknown_size == ("lat",)


def test_registry_rejects_duplicates_and_non_adapters():
    reg = AdapterRegistry()
    reg.register(MatplotlibAdapter())
    with pytest.raises(ValueError):
        reg.register(MatplotlibAdapter())
    with pytest.raises(TypeError):
        reg.register(object())


def test_request_validation():
    with pytest.raises(ValueError):
        RenderRequest(format="gif")
    with pytest.raises(ValueError):
        RenderRequest(width=0)
    with pytest.raises(ValueError):
        RenderRequest(units="cm")
    assert RenderRequest(width=800, height=600, units="px", dpi=200).size_in == (4.0, 3.0)
