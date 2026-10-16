"""Record (or list) the bytes main renders without a profile.

``CASES`` uses only APIs that exist on main, so the file runs unchanged
against a ``git archive`` of main::

    git archive origin/main | tar -x -C /tmp/main
    PYTHONPATH=/tmp/main python tests/render/data/record_main_hashes.py

which merges ``{matplotlib version: {case: sha256}}`` into
``main_render_hashes.json`` next to this file. The test
``test_no_profile_renders_keep_mains_bytes`` compares the branch with it.
"""

import hashlib
import json
from pathlib import Path

from ieeVisualizer import templates
from ieeVisualizer.network import render_network
from ieeVisualizer.render import RenderRequest, render
from ieeVisualizer.view import render_view
from ieeVisualizer.view.examples import reporting_pipeline, weekly_briefing


def _template(name, fmt):
    t = templates.get(name)
    return lambda: render(t.build_payload(t.example()), RenderRequest(format=fmt)).content


CASES = {f"{name}.{fmt}": _template(name, fmt)
         for name in ("timeseries", "bar_comparison", "funnel", "vector_field_2d")
         for fmt in ("svg", "png")}
# network templates: org_chart is left out on purpose - its labels moved to
# places clearly their own node's (docs/MOBILE_AND_EMAIL.md), so its bytes
# differ from main's by design
CASES.update({f"{name}.{fmt}": (lambda name=name, fmt=fmt: render_network(
    templates.get(name).build_payload(templates.get(name).example()),
    RenderRequest(format=fmt)).content)
    for name in ("network", "dependency_map") for fmt in ("svg", "png")})
CASES.update({f"view-{n}.{fmt}": (lambda b=b, fmt=fmt: render_view(b(), fmt).content)
              for n, b in (("briefing", weekly_briefing), ("pipeline", reporting_pipeline))
              for fmt in ("svg", "html", "png")})

if __name__ == "__main__":
    import matplotlib

    out = Path(__file__).with_name("main_render_hashes.json")
    data = json.loads(out.read_text()) if out.exists() else {}
    data[matplotlib.__version__] = {k: hashlib.sha256(f()).hexdigest() for k, f in CASES.items()}
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(matplotlib.__version__, len(CASES), "cases ->", out)
