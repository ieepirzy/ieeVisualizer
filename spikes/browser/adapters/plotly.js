// figureSpecToPlotly(spec, theme) -> {data, layout, config}. Same spike scope as
// adapters/echarts.js. Runs in the browser (Plotly needs a DOM to render).
import {
  UnsupportedSpec, rows, field, axisTitle, axisKind, displayTimezone, wallClockMs,
  numberFormatter, legendLabel, tokens, isolatedPoints, assignColors, barGrid,
} from './common.js';

// Plotly dates are naive wall-clock strings ("YYYY-MM-DD HH:MM:SS"), no zone.
const plotlyDate = (ms) => (ms == null ? null : new Date(ms).toISOString().replace('T', ' ').replace('Z', '').replace('.000', ''));

const hexAlpha = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
};

export function figureSpecToPlotly(spec, theme) {
  if ((spec.coordinate_system ?? 'cartesian2d') !== 'cartesian2d') throw new UnsupportedSpec('only cartesian2d in the spike');
  const tk = tokens(theme);
  const fmt = numberFormatter(theme);
  const tz = displayTimezone(spec);
  const colors = assignColors(spec, tk.palette);
  const xKind = axisKind(spec, 'x');
  const xVal = (v) => (xKind === 'time' ? plotlyDate(wallClockMs(v, tz)) : v);
  const lw = tk.lineWidth;
  const data = [], annotations = [], shapes = [];
  let xCategories = null, barmode;

  spec.layers.forEach((layer, li) => {
    const enc = layer.encoding || {};
    const xf = field(enc, 'x');
    const color = layer.style?.color ?? colors[layer.style?.color_of] ?? colors[layer.name] ?? tk.palette[0];
    const forecast = layer.role === 'forecast';
    const name = legendLabel(layer, `layer ${li}`);
    if (layer.type === 'line') {
      const yf = field(enc, 'y');
      const rs = rows(spec, layer.data);
      const ys = rs.map((r) => r[yf]);
      const lone = new Set(isolatedPoints(ys));
      data.push({ type: 'scatter', mode: lone.size ? 'lines+markers' : 'lines', name, connectgaps: false,
        x: rs.map((r) => xVal(r[xf])), y: ys, opacity: forecast ? tk.forecastOpacity : 1,
        line: { color, width: lw, dash: forecast ? `${(3.7 * lw).toFixed(1)}px,${(3.2 * lw).toFixed(1)}px` : 'solid' },
        marker: { color, size: ys.map((_, i) => (lone.has(i) ? 7 : 0)) } });
    } else if (layer.type === 'band') {
      const lo = field(enc, 'y_lower'), hi = field(enc, 'y_upper');
      const rs = rows(spec, layer.data);
      // fill:'tonexty' bridges a null (measured: the first version of this adapter
      // filled straight across day 25), so each contiguous run is its own
      // lower/upper trace pair, in one legend group.
      const runs = [];
      let cur = [];
      for (const r of rs) {
        if (r[lo] == null || r[hi] == null || r[xf] == null) { if (cur.length) runs.push(cur); cur = []; } else cur.push(r);
      }
      if (cur.length) runs.push(cur);
      runs.forEach((run, ri) => {
        const x = run.map((r) => xVal(r[xf]));
        data.push({ type: 'scatter', mode: 'lines', x, y: run.map((r) => r[lo]), line: { width: 0, color }, showlegend: false,
          hoverinfo: 'skip', legendgroup: name, name: `${name} lower` });
        data.push({ type: 'scatter', mode: 'lines', x, y: run.map((r) => r[hi]), line: { width: 0, color }, fill: 'tonexty',
          fillcolor: hexAlpha(color, tk.bandOpacity), name, legendgroup: name, showlegend: ri === 0 });
      });
    } else if (layer.type === 'bar') {
      if ((layer.orientation ?? 'vertical') !== 'vertical') throw new UnsupportedSpec('horizontal bars not in spike');
      const { cats, groups, grid } = barGrid(spec, layer);
      xCategories = cats;
      barmode = 'group';
      grid.forEach((cells, gi) => {
        data.push({ type: 'bar', name: String(groups[gi]), x: cats, y: cells.map((c) => c.value),
          marker: { color: tk.palette[gi % tk.palette.length] } });
        cells.forEach((c, ci) => {
          if (c.value != null) return;
          annotations.push({ x: ci - 0.4 + (gi + 0.5) * (0.8 / groups.length), y: 0, xref: 'x', yref: 'y',
            text: 'no data', textangle: -90, showarrow: false, yanchor: 'bottom', yshift: 4,
            font: { color: tk.muted, size: tk.captionPx, family: tk.family } });
        });
      });
    } else if (layer.type === 'heatmap') {
      const yf = field(enc, 'y'), cf = field(enc, 'color');
      const rs = rows(spec, layer.data);
      const xs = [...new Set(rs.map((r) => r[xf]))], ys = [...new Set(rs.map((r) => r[yf]))];
      const z = ys.map(() => xs.map(() => null));
      for (const r of rs) z[ys.indexOf(r[yf])][xs.indexOf(r[xf])] = r[cf];
      const unit = spec.datasets[layer.data].columns[cf].unit;
      data.push({ type: 'heatmap', x: xs, y: ys, z, hoverongaps: false, name,
        colorscale: tk.sequential.map((c, i) => [i / (tk.sequential.length - 1), c]),
        colorbar: { title: { text: unit ?? cf }, tickfont: { family: tk.family } } });
    } else {
      throw new UnsupportedSpec(`mark ${layer.type} not in spike`);
    }
  });

  for (const a of spec.annotations || []) {
    if (a.type !== 'reference_line' || a.axis !== 'y') throw new UnsupportedSpec(`annotation ${a.type}/${a.axis}`);
    shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: a.value, y1: a.value,
      line: { color: tk.referenceColor, width: 1.2, dash: 'dashdot' } });
    if (a.label) annotations.push({ xref: 'paper', x: 1, yref: 'y', y: a.value, text: a.label, showarrow: false,
      xanchor: 'right', yanchor: 'bottom', font: { color: tk.muted, size: tk.captionPx, family: tk.family } });
  }
  if (spec.caption) annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 0, xanchor: 'left', yanchor: 'top',
    yshift: -68, text: `<i>${spec.caption}</i>`, showarrow: false, font: { color: tk.muted, size: tk.captionPx, family: tk.family } });

  const loc = theme?.locale;
  const yLimits = spec.axes?.y?.limits ?? [null, null];
  const axis = { color: tk.foreground, gridcolor: tk.grid, linecolor: tk.foreground, showline: true, ticks: 'outside', zeroline: false };
  const df = loc?.date_formats;
  const layout = {
    width: 800, height: 500, paper_bgcolor: tk.background, plot_bgcolor: tk.background,
    font: { family: tk.family, size: tk.fontPx, color: tk.foreground },
    // Plotly: first char = decimal, second = thousands.
    separators: loc ? `${loc.decimal}${loc.group}` : undefined,
    title: { text: spec.title, x: 0.5, font: { size: tk.fontPx * 1.2 } },
    margin: { l: 80, r: 30, t: 50, b: 95 },
    legend: { bordercolor: tk.grid, borderwidth: 1 },
    barmode, annotations, shapes, showlegend: true,
    xaxis: { ...axis, title: { text: axisTitle(spec, 'x') },
      ...(xKind === 'time' && df ? { tickformatstops: [
        { dtickrange: [null, 86400000], value: df[3] }, { dtickrange: [86400000, 'M1'], value: df[2] },
        { dtickrange: ['M1', 'M12'], value: df[1] }, { dtickrange: ['M12', null], value: df[0] }] } : {}),
      ...(xCategories ? { type: 'category' } : {}) },
    yaxis: { ...axis, title: { text: axisTitle(spec, 'y') },
      // A one-sided limit: autorange, but never past the fixed end.
      ...(yLimits[0] != null || yLimits[1] != null ? { autorange: true, autorangeoptions: {
        ...(yLimits[0] != null ? { minallowed: yLimits[0], include: [yLimits[0]] } : {}),
        ...(yLimits[1] != null ? { maxallowed: yLimits[1], include: [yLimits[1]] } : {}) } } : {}),
      ...(loc ? { tickformat: ',d' } : {}) },
  };
  if (spec.layers.some((l) => l.type === 'heatmap')) {
    // Plotly auto-types axes from the values: "08", "10" become numbers 8, 10
    // (measured) unless the type is forced.
    Object.assign(layout.xaxis, { type: 'category', showgrid: false });
    Object.assign(layout.yaxis, { type: 'category', showgrid: false, tickformat: undefined });
  }
  // Fixed trace uids: Plotly otherwise draws random ones into ids/classes (measured),
  // which makes two renders of one spec differ.
  data.forEach((t, i) => { t.uid = `fs${i}`; });
  return { data, layout, config: { staticPlot: false, displaylogo: false, responsive: false } };
}
