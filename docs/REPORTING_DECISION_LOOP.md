# Reporting as an information-to-decision workflow

**Product rationale and use case, 2026-10-15.** Companion to [VISION_AND_ARCHITECTURE.md](VISION_AND_ARCHITECTURE.md) and [CONVERSATIONAL_UI_AND_REPORTING.md](CONVERSATIONAL_UI_AND_REPORTING.md).

## The observation

A substantial share of knowledge work consists of transforming existing information into forms another person can understand and act upon: querying records, copying figures into spreadsheets, making charts, assembling slide decks, writing narrative summaries, distributing reports, and answering follow-up questions. The value is not in producing the file itself. It is in reducing the effort and delay between **what happened**, **why it matters**, **what remains uncertain**, and **what to do next**.

An example: an analyst extracts bookings and revenue, charts weekly trends, writes why conversion may have changed, sends the report to management, and then gets asked to break down the change by campaign, region or cohort. The expensive part is often not plotting. It is moving back and forth between evidence, computation, presentation, explanation and investigation.

**ieeVisualizer should make this translation and revisiting of information inexpensive, repeatable and interactive.**

## The complete loop

```text
Authoritative raw evidence
          |
Valid transformations and domain analysis
          |
Composed visual representation (ieeVisualizer)
          |
Explanation with provenance and uncertainty
          |
A human or agent understands, questions or decides
          |
Follow-up drill-down, comparison, alternate perspective
          |
Updated understanding and possible action
          |
Measure consequences and feed back into future reports
```

This is an iterative cycle rather than a one-way “produce PDF” pipeline. Visualizations should remain **reopenable, editable and addressable objects**. A recipient can ask “what changed in conversion?”, inspect the relevant series, switch cohorts, highlight unusual periods, and obtain a newly rendered view using the same source references, semantic specification and permission boundary. The follow-up should not require manually reconstructing the analysis from a screenshot.

## Concrete Muutto365 scenario

A Mirarun worker prepares a founder-facing weekly or every-two-weeks company briefing. Domain systems provide authorized booking, marketing, revenue and operational datasets; specialized analytics perform the substantive calculations; ieeVisualizer selects reusable company-branded figure, diagram and layout templates, binds the evidence, renders assets for email and produces an optionally interactive web view. The delivery workflow handles review, permissions, recipients and email.

Eemeli might notice lower conversion and ask why. An agent then opens the report's visualization artifacts, compares the time window with preceding periods, segments by campaign/source and customer cohort where the data permits, evaluates whether latency, traffic quality, price or attribution changed, and reports supported findings with uncertainty. It may highlight a likely cause and recommend further verification. **A correlation is not automatically a causal explanation**; unknowns must remain visible.

An example of successful output is not “a report was sent” but “a founder identified a specific issue and made a better-informed decision with minimal extra analyst effort.”

The report cadence is a **host-owned scheduling decision**. Weekly, every two weeks and previously considered other frequencies are not a commitment embedded in ieeVisualizer. Nor is ieeVisualizer responsible for gathering privileged data, building all statistics, approving a business action or sending email.

## Why reusable visual artifacts matter

A flat image is a terminal output. A persistent visualization artifact retains a structured view spec, component/template identity, parameters, data/source references, provenance, render versions and (where relevant) interaction state. This makes it possible to:

- Export the same view as an email-safe PNG/SVG, printable PDF figure or browser-interactive component.
- Restyle for another audience or report without regenerating arbitrary UI source.
- Ask a follow-up, apply a filter, drill into a node, compare periods, change dimensional slices or emphasize an outlier.
- Distinguish the original frozen reporting snapshot from a refreshed analysis.
- Reproduce exactly what recipients saw, subject to source retention and permissions.
- Compose charts alongside workflows, dependency diagrams, text, mathematical expressions, annotations and interactive controls.

This applies beyond business reports to scientific work, research presentations, simulations, logistics processes and operational diagnosis. Reporting is a compelling first consumer of the general system, **not the product's identity**.

## Agent workflow and responsibilities

**An agent can:** choose an appropriate representation, compose registered components, supply validated parameters, request data from authorized owners, invoke analysis tools, render and visually inspect results, compose evidence-linked narratives, propose drill-downs and export publication assets.

**ieeVisualizer owns:** visual semantics, component/template registration, view state, renderer capability matching, visual artifact manifests, presentation composition, interaction primitives and rendering/export.

**Other systems own:** authoritative sources, numerical/statistical/economic computations, identity and permissions, agent orchestration, review and sign-off, report delivery, retention policy and subsequent operational action.

Use existing component primitives by default. Bespoke frontend source generation is a rare fallback for genuinely unexpressible interactions, with sandboxing, validation and possible later promotion to a reusable component.

## Anti-goal: autonomous bureaucracy

A system that generates hundreds of polished reports nobody reads merely automates administrative waste. Optimize for **information gain and decision usefulness**, not output volume, number of charts, document length or automation count.

Good defaults:
1. Include a concise “what changed / why it matters / what is uncertain / what warrants action” layer.
2. Use charts or diagrams only when they communicate better than concise text or a table.
3. Make the supporting evidence and assumptions inspectable.
4. Allow recipients to pursue follow-up questions without restarting manual analysis.
5. Distinguish monitoring (routine updates) from exceptions (urgent deviations) and exploratory questions.
6. Prefer human approval for external distribution, sensitive data and consequential recommendations.
7. Measure usefulness through questions resolved, time to explanation, decisions supported and follow-up effort, not reports produced.

## Research and evaluation questions

- Can a recipient go from an observation to a defensible segmented explanation using the original artifact without rebuilding the visualization?
- How reliably can a second agent reconstruct a figure from its manifest and frozen snapshot?
- Which chart/diagram/template families account for most genuine reporting needs?
- Do interactive reports actually shorten the loop from identifying an anomaly to understanding it, compared with static PDFs?
- Do summaries make uncertainty and absent evidence salient instead of hiding them?
- Can the first useful report be shipped without requiring a complete dashboard, analytics or code-generation platform?

**Thesis:** ieeVisualizer succeeds when visual information becomes a reusable interface between evidence and reasoning. The aspiration is not automated PowerPoint. It is lower-friction, higher-integrity analytical work between decisions.
