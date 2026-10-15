"""Self-contained static HTML for a ViewSpec (see :mod:`.render`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..spec._common import ptr_join
from .draw import Embed, esc, to_svg
from .flow import flow_outline
from .layout import GAP, edge_text, flow_items, kpi_lines, table_cells
from .model import Card, FigureComponent, Flow, Grid, Kpi, Node, Note, Stack, Table, Text, ViewSpec
from .text import NO_DATA, NULL_CELL, Run, parse_markdown

if TYPE_CHECKING:
    from .render import FigureRenderer

CSS = """
body{margin:0;background:#ffffff;color:#1f2328;
font-family:"DejaVu Sans",Verdana,Arial,sans-serif;font-size:15px;line-height:1.45}
.iv-view{max-width:%(w)dpx;margin:0 auto;padding:24px;box-sizing:border-box}
.iv-view h1{font-size:26px;margin:0 0 16px}
.iv-col>*+*{margin-top:16px}
.iv-row{display:grid;gap:16px;align-items:start}
.iv-card{border:1px solid #d0d7de;border-radius:10px;padding:16px}
.iv-card>h2{font-size:16px;margin:0 0 12px}
.iv-text p,.iv-text ul{margin:0 0 6px}
.iv-heading{font-size:20px;font-weight:bold}.iv-lead{font-size:18px}
.iv-small{font-size:12px;color:#57606a}
.iv-kpi{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:14px 16px}
.iv-kpi-label{font-size:13px;color:#57606a}
.iv-kpi-value{font-size:30px;font-weight:bold;margin:4px 0}
.iv-kpi-value.iv-nodata{font-size:22px;font-weight:normal;font-style:italic;color:#57606a}
.iv-kpi-cmp{font-size:12px}.iv-kpi-src{font-size:12px;color:#57606a}
.iv-table{border-collapse:collapse;width:100%%;font-size:13px}
.iv-table caption{text-align:left;font-weight:bold;font-size:15px;padding-bottom:6px}
.iv-table th{background:#eaeef2;text-align:left}
.iv-table th,.iv-table td{padding:4px 10px}
.iv-table tbody tr:nth-child(even){background:#f6f8fa}
.iv-num{text-align:right}.iv-null{color:#57606a}
.iv-caveat{background:#fff8c5;border-left:4px solid #bf8700;padding:10px 12px;font-size:13px}
.iv-footnote{font-size:12px;color:#57606a;border-top:1px solid #d0d7de;padding-top:6px;
margin:0;max-width:100%%}
.iv-figure,.iv-flow{margin:0}.iv-figure svg,.iv-flow svg{display:block;max-width:100%%;
height:auto}
.iv-flow figcaption{font-weight:bold;margin-bottom:6px}
.iv-flow details{font-size:13px;margin-top:6px}
"""


def _runs(runs: list[Run]) -> str:
    out = []
    for r in runs:
        s = esc(r.text)
        if r.code:
            s = f"<code>{s}</code>"
        if r.italic:
            s = f"<em>{s}</em>"
        if r.bold:
            s = f"<strong>{s}</strong>"
        out.append(s)
    return "".join(out)


def markdown_html(text: str) -> str:
    out, items = [], []
    for kind, runs in parse_markdown(text) + [("end", [])]:
        if kind == "li":
            items.append(f"<li>{_runs(runs)}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        if kind == "p":
            out.append(f"<p>{_runs(runs)}</p>")
    return "".join(out)


class _Html:
    def __init__(self, view: ViewSpec, figs: FigureRenderer):
        self.view, self.figs, self.n = view, figs, 0

    def node(self, n: Node, path: str, w: float) -> str:
        if isinstance(n, (Stack, Grid, Card)):
            kids = list(enumerate(n.children))
            inner_w = w - 34 if isinstance(n, Card) else w
            cols = (max(1, n.columns) if isinstance(n, Grid) else
                    len(kids) if isinstance(n, Stack) and n.direction == "horizontal" else 1)
            cw = (inner_w - GAP * (cols - 1)) / max(cols, 1)
            body = "".join(self.node(c, ptr_join(path, "children", i), cw) for i, c in kids)
            if cols > 1:
                body = (f'<div class="iv-row" style="grid-template-columns:'
                        f'repeat({cols},minmax(0,1fr))">{body}</div>')
            else:
                body = f'<div class="iv-col">{body}</div>'
            if isinstance(n, Card):
                title = f"<h2>{esc(n.title)}</h2>" if n.title else ""
                return f'<section class="iv-card">{title}{body}</section>'
            return body
        if isinstance(n, FigureComponent):
            spec = n.spec if n.spec is not None else self.view.figures[n.figure]
            pointer = (ptr_join(path, "spec") if n.spec is not None
                       else ptr_join("/figures", n.figure))
            e = Embed(0, 0, w, round(w * n.aspect, 2), spec, pointer, index=self.n)
            self.n += 1
            vb, inner = self.figs.svg_parts(e)
            label = esc(spec.title or "Figure")
            return (f'<figure class="iv-figure"><svg xmlns="http://www.w3.org/2000/svg" '
                    f'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="{vb}" width="100%" '
                    f'role="img" aria-label="{label}">{inner}</svg></figure>')
        if isinstance(n, Text):
            return f'<div class="iv-text iv-{esc(n.style)}">{markdown_html(n.text)}</div>'
        if isinstance(n, Note):
            if n.type == "footnote":
                return f'<div class="iv-footnote">{markdown_html(n.text)}</div>'
            return f'<aside class="iv-caveat">{markdown_html("**Caveat:** " + n.text)}</aside>'
        if isinstance(n, Kpi):
            value, cmp = kpi_lines(n)
            cls = "iv-kpi-value iv-nodata" if n.value is None else "iv-kpi-value"
            parts = [f'<div class="iv-kpi-label">{esc(n.label)}</div>',
                     f'<div class="{cls}">{esc(NO_DATA if n.value is None else value)}</div>']
            if cmp:
                parts.append(f'<div class="iv-kpi-cmp">{esc(cmp)}</div>')
            if n.source:
                parts.append(f'<div class="iv-kpi-src">Source: {esc(n.source)}</div>')
            return f'<div class="iv-kpi">{"".join(parts)}</div>'
        if isinstance(n, Table):
            return self.table(n, path)
        return self.flow(n, path)

    def table(self, n: Table, path: str) -> str:
        ds = self.view.datasets[n.data]
        names, heads, rows, total = table_cells(n, ds, path)
        num = [ds.columns[c].dtype in ("int", "float") for c in names]
        cap = f"<caption>{esc(n.title)}</caption>" if n.title else ""
        num_cls = ' class="iv-num"'
        th = "".join(f'<th scope="col"{num_cls if num[i] else ""}>{esc(h)}</th>'
                     for i, h in enumerate(heads))
        body = []
        for r in rows:
            tds = []
            for i, s in enumerate(r):
                cls = " ".join(c for c, on in (("iv-num", num[i]), ("iv-null", s == NULL_CELL))
                               if on)
                tds.append(f'<td class="{cls}">{esc(s)}</td>' if cls else f"<td>{esc(s)}</td>")
            body.append("<tr>" + "".join(tds) + "</tr>")
        more = (f'<p class="iv-small">Showing {len(rows)} of {total} rows</p>'
                if len(rows) < total else "")
        return (f'<table class="iv-table">{cap}<thead><tr>{th}</tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table>{more}')

    def flow(self, f: Flow, path: str) -> str:
        items, gw, gh, order = flow_items(f)
        svg = to_svg(items, gw, gh, lambda e: "", xml_header=False, css_width="100%")
        svg = svg.replace(' role="img">', f' role="img" aria-label="{esc(f.title or "Flow")}">',
                          1)
        cap = f"<figcaption>{esc(f.title)}</figcaption>" if f.title else ""
        desc = f"<p>{esc(f.description)}</p>" if f.description else ""
        lis = "".join(f"<li>{esc(line.split('. ', 1)[1])}</li>" for line in flow_outline(f, order))
        claims = [edge_text(e) for e in f.edges if e.kind == "causal"]
        note = (f'<p class="iv-small">{len(claims)} causal link(s) are claims, not measured '
                "effects.</p>" if claims else "")
        return (f'<figure class="iv-flow" data-flow="{esc(path)}">{cap}{svg}'
                f"<details><summary>Text version</summary>{desc}<ol>{lis}</ol>{note}</details>"
                "</figure>")


def render_html(view: ViewSpec, figs: FigureRenderer, width: float) -> str:
    h = _Html(view, figs)
    body = h.node(view.root, "/root", width - 48)
    title = esc(view.title or "View")
    heading = f"<h1>{esc(view.title)}</h1>" if view.title else ""
    lang = view.metadata.get("lang") if isinstance(view.metadata.get("lang"), str) else "en"
    return (f'<!DOCTYPE html>\n<html lang="{esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{title}</title>\n<style>{CSS % {'w': int(width)}}</style>\n</head>\n"
            f'<body>\n<main class="iv-view">{heading}{body}</main>\n</body>\n</html>\n')
