# Conversational visualization and agent-generated reporting

**Research and architecture addendum — 2026-10-15.** Companion to [VISION_AND_ARCHITECTURE.md](VISION_AND_ARCHITECTURE.md). This captures an explicit product use case and draws *publicly documented* technical lessons from OpenAI's inline visualization/chat UI, distinguishing what is documented from inferred architecture.

## 1. Company reporting is a first-class consumer

ieeVisualizer will be the **visualization service/library invoked by agents that assemble Muutto365 weekly or every-two-weeks company reports**, plus potentially other cadences. Agents receive governed, structured evidence from underlying services, determine suitable visual forms, compose figures and supporting explanatory elements, export publication-ready assets, and include them in organizational briefings. ieeVisualizer is responsible for **visual expression and interactive exploration**, not data authority, report scheduling, report editorial sign-off, permission policy, mail delivery or primary analytics computation.

Reports may have differing audiences and scopes (founders initially, later functions/shareholders), and the host reporting workflow must filter source data by recipient permission *before* preparing view data. The cadence (weekly vs bi-weekly, and whether bi-weekly means every other week or twice weekly) remains a scheduling decision for the report owner. Previous tri-weekly exploration is another contemplated cadence, not a commitment. This is an example integration, not a reason to specialize the product solely for reporting.

### Reference workflow

```text
 Mirarun scheduled reporting worker / other agent
        |
 authorized querying of finance/CRM/booking/AdIntel/ops/telemetry
        |
 normalized evidence and computed metrics (source-owned)
        |
  choose template(s) and render with ieeVisualizer
        |
 inspect resulting plot(s) + validate values/citations
        |
 compose report sections + narrative + visuals
        |
 approval / permissions / publication policy (host-owned)
        |
 static SVG/PNG/PDF/email images | interactive web-report figures
        |
 shared email service / report web publication
```

**Reporting examples:** revenue and margin over time, bookings by stage, conversion funnels, campaign CPA/ROAS by channel with correct attribution windows, support/operations capacity, delivery completion and latency, quote-to-booking cohort analysis, agent activity, costs, forecasts and uncertainty. These are *examples*, not claims that all such authoritative data is currently available or that every report should expose all of it.

### Additional needs beyond generic plotting

- **Consistent corporate visual identity:** brand themes, accessible typography/contrast, standardized legends, dates and chart captions.
- **Templates at two scales:** (a) figure template: conversion funnel or revenue trend; (b) report composition template: title, executive findings, figures, evidence and caveats. The publication system ultimately owns the report.
- **Confidence and provenance:** every metric must show relevant source, unit, denominator, aggregation window, date/timezone, version and refresh timestamp; indicate observations vs forecasts. Never fabricate precision, uncertainty intervals, causal assertions or missing values.
- **Visual quality gate:** agents should inspect the rendered result (not only a spec), catch label collisions, clipping, deceptive axes, missing series, distorted comparisons and accessibility problems.
- **Multiple exports from a common spec:** email-safe raster, scalable vector, interactive HTML/view state, optionally PDF. An email does not run JavaScript; link to a richer interactive report where appropriate.
- **Repeatability and comparison:** preserve report-run ID, figure spec, dataset/query snapshot references, template version and render manifest. Historical report figures must not silently change when a source is updated.
- **Fail gracefully:** incomplete sources, partial runs and unsupported renderer capabilities require visible warnings or an omitted figure, never silently invented numbers.

## 2. OpenAI inspiration: public evidence

As of 2026-10-15, these are relevant **publicly documented product capabilities** (not access to ChatGPT's private rendering internals):

1. OpenAI **March 10, 2026**: dynamic visual explanations for 70+ math/science topics, real-time manipulation of variables and visible effects on formulas/graphs. https://openai.com/index/new-ways-to-learn-math-and-science-in-chatgpt/
2. ChatGPT data analysis: bar/line/pie/scatter interactive charts, other static chart types, interactive-to-static switching and chart export/customization. https://help.openai.com/en/articles/8437071-advanced-data-
3. OpenAI **ChatKit widgets**: structured WidgetRoot containers, composable WidgetNode components, templating, generated widget JSON and backend-provided content. https://developers.openai.com/api/docs/guides/chatkit-widgets
4. **ChatKit custom integrations and actions:** streamed widget delivery, action payloads, client/server handling, in-place or streamed updates from interactions. https://developers.openai.com/api/docs/guides/custom-chatkit and https://developers.openai.com/api/docs/guides/chatkit-actions
5. **Apps SDK/MCP Apps UI:** embedded inline app surfaces, scoped views and style integration with hosts; open bridge conventions with host-specific extensions. https://developers.openai.com/plugins/concepts/ui-guidelines and https://developers.openai.com/plugins/reference
6. OpenAI's **Data plugin** describes refinable interactive dashboards and business-data analysis, an adjacent product category; not a dependency or proof of ChatGPT's internal architecture. https://help.openai.com/en/articles/20001518-using-the-data-plugin-in-chatgpt-work-and-codex

### What we know vs what we infer

**Documented:** ChatGPT can show interactive charts and mathematical modules inline; ChatKit developers can construct widget trees and stream updates; widget actions can be routed back to server or client; apps can provide inline interactive UI.

**Inference / product hypothesis:** structured UI definitions, a renderer chosen by the host, and typed actions/state make agent interfaces substantially more controllable, reproducible and secure than unconstrained generated frontend code. **Do not claim** these public interfaces are precisely how ChatGPT's private native charts or math modules are built.

## 3. ieeVisualizer adoption pattern

Adopt a **host-neutral view specification with composable primitives**, with adapters for chat, standalone interactive web, programmatic scientific work, scheduled company reports and offline artifacts.

```text
     Agent intent + authorized data
                 |
       Template / Component registry
                 |
       versioned FigureSpec / ViewSpec
                 |
         renderer capability matching
           /          |           \
       inline      interactive    publication
       widget       web view       PNG/SVG/PDF
           \          |           /
             spec + artifact manifest
```

Suggested component taxonomy:
- **Analytical visuals:** cartesian charts, distribution views, surfaces, meshes, flows, projections, timelines.
- **Context components:** KPI tiles, insight sentences, provenance links, footnotes, formulas, mathematical typesetting, tables.
- **Controls:** sliders, selectors, range/zoom, dataset switches, filters, camera angles, compare modes.
- **Composition:** cards, grids, split views, sequence/step panels, guided narrative.
- **Feedback:** loading/progress/error/empty/quality warnings with specific semantics.
- **Interactive actions:** `change_parameter`, `select_series`, `set_range`, `focus_point`, `compare`, `export`, `inspect_source`, `snapshot` etc. These are *conceptual commands*, not committed API identifiers.

### Thin viable demonstration (report-oriented)

An authorized agent gets a structured dataset of weekly booking activity and conversion metrics; selects two known templates (time series and funnel), renders a consistent themed HTML view, exports both figures as image files suitable for email, and produces an artifact manifest with source identifiers and figure settings. A human can inspect/edit the same figures in an interactive browser view. Confirm that a rerender gives the same labels/units/numbers from a frozen dataset snapshot.

This **should not leapfrog** baseline library fixes or become a full report-writing, dashboarding, finance modeling, email-sending or workflow orchestration package.

## 4. Architecture decisions to test, not assume

- Does the host need the whole ViewSpec, or should an inline chat widget reference a compact pre-rendered figure or a figure runtime?
- Which visualization interactions run entirely client-side, and which need authenticated backend recomputation?
- Can the same FigureSpec be rendered static and interactive without misleading differences?
- Can agents patch figures and component parameters while users retain meaningful state ownership?
- How can figure snapshots, large-source references and hosted artifacts remain reproducible without expensive unnecessary egress?
- What verification boundary prevents unsupported visual claims and fabricated report narratives?

**Design directive:** emulate the *composability and inline interaction* of modern conversational UIs, **not** a proprietary ChatGPT component syntax. ieeVisualizer must remain useful outside any one chat provider.
