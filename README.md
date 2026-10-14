# ieeVisualizer

A unified, agent-accessible framework for **mathematical, scientific, multidimensional, economic, realtime and generative visualization**.

Originally conceived as a simpler interface over `matplotlib.pyplot`, ieeVisualizer's longer-term direction is a coherent visualization layer over specialist plotting, interactive, 3D and animation backends. It focuses on figures, reusable components and templates, declarative visualization specifications, human/agent-guided exploration, and portable visualization artifacts.

**[Read the consolidated vision and architecture](docs/VISION_AND_ARCHITECTURE.md).** See also **[conversational UI and agent-generated company reporting](docs/CONVERSATIONAL_UI_AND_REPORTING.md)** for the OpenAI-inspired interaction model and recurring-report workflow. The **[reporting decision loop](docs/REPORTING_DECISION_LOOP.md)** captures the product rationale: evidence → understanding → follow-up → decision, without automating pointless report production.

> **Project status:** early-stage. The vision describes planned capabilities, not features already implemented. See [existing issues](https://github.com/ieepirzy/ieeVisualizer/issues) for immediate baseline work.

ieeVisualizer is **not** a Grafana replacement or an evaluation/numerical computing engine. **ieeVal and ieeLabTools are independent projects** that may produce results for visualization; neither is a prerequisite for ieeVisualizer.

## Development priorities

1. Make the existing Python/Matplotlib functions installable, tested and functional.
2. Introduce typed figure specifications, reusable components/templates and provenance-aware exports.
3. Add interactive/browser and agent-facing adapters with persistent visualization state.
4. Validate realtime and scientific/multidimensional workflows before adding further rendering engines.

See the architecture document for backend candidates, live-session concepts, generative UI boundaries, integration scenarios and unresolved design decisions.
