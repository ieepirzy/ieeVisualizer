"""examples/mobile_gallery.py renders every template and gallery view at the
phone and tablet profiles and lists what is not legible."""

import importlib.util
import json
from pathlib import Path

from ieeVisualizer import templates

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_mobile_gallery_covers_every_template(tmp_path):
    spec = importlib.util.spec_from_file_location("mobile_gallery", EXAMPLES / "mobile_gallery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.main(tmp_path)
    for t in templates.list_templates():
        for profile in ("email-phone", "email-tablet"):
            assert (tmp_path / f"{t['name']}--{profile}.png").stat().st_size > 0
    for view in module.VIEWS:
        assert (tmp_path / f"view-{view}.email.html").exists()
    assert json.loads((tmp_path / "legibility.json").read_text()) == report
    # every template and view passes the send gate at both email profiles
    assert {file: ws for file, ws in report.items() if ws} == {}
