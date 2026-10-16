"""Phone and tablet renders of every template and gallery view, for looking at.

Run: python examples/mobile_gallery.py  (writes to examples/out/mobile/, gitignored)

For every registered template's example (FigureSpec and network templates)
it writes ``<template>--<profile>.png`` at the ``email-phone`` and
``email-tablet`` profiles (docs/MOBILE_AND_EMAIL.md). For every gallery view
it writes ``view-<name>--<profile>.png`` and the email version,
``view-<name>.email.html`` plus the images it references
(``view-<name>.email-<image>.png``; open the HTML in a browser and narrow
the window to 360 px to see the phone layout). ``legibility.json`` lists
every warning the send gate (``check_legible``) refuses, per file: the renders are not filtered,
so an entry there is something a reader would see.
"""

from __future__ import annotations

import json
from pathlib import Path

from ieeVisualizer import templates
from ieeVisualizer.network import NetworkSpec, render_network
from ieeVisualizer.render import GATING_CODES, get_profile, render
from ieeVisualizer.view import render_email, render_view
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing

OUT = Path(__file__).parent / "out" / "mobile"
PROFILES = ("email-phone", "email-tablet")
VIEWS = {"reporting-pipeline": reporting_pipeline, "weekly-briefing": weekly_briefing}


def _report(warnings) -> list[dict]:
    return [w.to_dict() for w in warnings if w.code in GATING_CODES]


def main(out: Path | None = None) -> dict[str, list[dict]]:
    """Write every render to ``out``; returns ``{file: legibility warnings}``."""
    out = Path(out) if out is not None else OUT
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict]] = {}
    for entry in templates.list_templates():
        t = templates.get(entry["name"])
        spec = t.build_payload(t.example())
        for name in PROFILES:
            req = get_profile(name).figure_request("png")
            result = render_network(spec, req) if isinstance(spec, NetworkSpec) \
                else render(spec, req)
            file = f"{t.name}--{name}.png"
            (out / file).write_bytes(result.content)
            report[file] = _report(result.warnings)
    for view_name, build in VIEWS.items():
        view = build()
        for name in PROFILES:
            file = f"view-{view_name}--{name}.png"
            result = render_view(view, "png", profile=name)
            (out / file).write_bytes(result.content)
            report[file] = _report(result.warnings)
        prefix = f"view-{view_name}.email"
        email = render_email(view, "email-phone", image_src=lambda n, p=prefix: f"{p}-{n}")
        (out / f"{prefix}.html").write_text(email.html, encoding="utf-8")
        for image, content in email.images.items():
            (out / f"{prefix}-{image}").write_bytes(content)
        report[f"{prefix}.html"] = _report(email.warnings)
    (out / "legibility.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    for file, ws in sorted(report.items()):
        print(f"{file:52s} {', '.join(sorted({w['code'] for w in ws})) or '-'}")
    return report


if __name__ == "__main__":
    main()
