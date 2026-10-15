// figureSpecToECharts(spec, theme) -> ECharts option. Spike scope: line (nulls,
// forecast role, isolated points), grouped bar (null/missing slots labelled
// "no data"), heatmap (null cells blank), provisional band mark, y reference
// line, time / category / linear axes. Everything else throws UnsupportedSpec.
import {
  UnsupportedSpec, rows, field, axisTitle, axisKind, displayTimezone, wallClockMs,
  numberFormatter, legendLabel, tokens, isolatedPoints, assignColors, barGrid, describe,
} from './common.js';

// strftime (theme locale date patterns) -> ECharts time template.
const strftimeToEC = (p) => p.replace(/%Y/g, '{yyyy}').replace(/%m/g, '{MM}').replace(/%d/g, '{dd}')
  .replace(/%H/g, '{HH}').replace(/%M/g, '{mm}').replace(/%S/g, '{ss}');

function timeLabelFormatter(theme) {
  const f = theme?.locale?.date_formats; // [year, month, day, hour, minute, second]
  if (!f) return undefined;
  return { year: strftimeToEC(f[0]), month: strftimeToEC(f[1]), day: strftimeToEC(f[2]),
    hour: strftimeToEC(f[3]), minute: strftimeToEC(f[4]), second: strftimeToEC(f[5].replace(',%f', '')) };
}

// matplotlib's "dashed" is (3.7, 1.6) x linewidth; forecast doubles every gap (RENDERING.md, Roles).
const forecastDash = (lw) => [3.7 * lw, 3.2 * lw];

export function figureSpecToECharts(spec, theme) {
  if ((spec.coordinate_system ?? 'cartesian2d') !== 'cartesian2d') throw new UnsupportedSpec('only cartesian2d in the spike');
  const tk = tokens(theme);
  const fmt = numberFormatter(theme);
  const tz = displayTimezone(spec);
  const colors = assignColors(spec, tk.palette);
  const xKind = axisKind(spec, 'x');
  const text = { color: tk.foreground, fontFamily: tk.family, fontSize: tk.fontPx };
  const series = [];
  const legend = [];
  let categoryX = null, categoryY = null, visualMap = null;

  const xVal = (v) => (xKind === 'time' ? wallClockMs(v, tz) : v);

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
      series.push({
        type: 'line', name, color, showSymbol: true, symbol: 'circle', symbolSize: 0,
        connectNulls: false, // explicit, although it is ECharts' default
        lineStyle: { width: tk.lineWidth, type: forecast ? forecastDash(tk.lineWidth) : 'solid',
          opacity: forecast ? tk.forecastOpacity : 1 },
        itemStyle: { color, opacity: forecast ? tk.forecastOpacity : 1 },
        // A null y is '-' (ECharts' "no value"), never 0. Lone points get a marker.
        data: rs.map((r, i) => ({ value: [xVal(r[xf]), r[yf] ?? '-'], ...(lone.has(i) ? { symbolSize: 6 } : {}) })),
      });
      legend.push(name);
    } else if (layer.type === 'band') {
      const lo = field(enc, 'y_lower'), hi = field(enc, 'y_upper');
      const rs = rows(spec, layer.data);
      // Contiguous runs of rows with both bounds present; a null bound breaks the band.
      const runs = [];
      let cur = [];
      for (const r of rs) {
        if (r[lo] == null || r[hi] == null || r[xf] == null) { if (cur.length) runs.push(cur); cur = []; continue; }
        cur.push([xVal(r[xf]), r[lo], r[hi]]);
      }
      if (cur.length) runs.push(cur);
      const fill = { fill: color, opacity: tk.bandOpacity };
      // One data item per row (so the y axis extent includes both bounds: the
      // first version encoded nothing and the band ran outside the grid); the
      // polygon of a run is drawn by the run's first row.
      const firstOfRun = new Map(runs.map((run) => [run[0], run]));
      series.push({
        type: 'custom', name, color, silent: false, z: 1, clip: true,
        data: runs.flat(), encode: { x: 0, y: [1, 2] },
        renderItem: (params, api) => {
          const run = firstOfRun.get(runs.flat()[params.dataIndex]);
          if (!run) return null;
          const top = run.map((p) => api.coord([p[0], p[2]]));
          const bottom = run.map((p) => api.coord([p[0], p[1]])).reverse();
          if (run.length === 1) { // a lone interval: a vertical bar, not nothing
            const [x, y1] = top[0]; const [, y0] = bottom[0];
            return { type: 'rect', shape: { x: x - 2, y: y1, width: 4, height: y0 - y1 }, style: fill };
          }
          return { type: 'polygon', shape: { points: top.concat(bottom) }, style: fill };
        },
      });
      legend.push(name);
    } else if (layer.type === 'bar') {
      if ((layer.orientation ?? 'vertical') !== 'vertical') throw new UnsupportedSpec('horizontal bars not in spike');
      const { cats, groups, grid } = barGrid(spec, layer);
      categoryX = cats;
      grid.forEach((cells, gi) => {
        series.push({ type: 'bar', name: String(groups[gi]), color: tk.palette[gi % tk.palette.length],
          data: cells.map((c) => (c.value == null ? '-' : c.value)), barGap: '0%', barCategoryGap: '20%' });
        legend.push(String(groups[gi]));
      });
      // "no data" in each empty slot, placed where its bar would be.
      const missing = [];
      grid.forEach((cells, gi) => cells.forEach((c, ci) => { if (c.value == null) missing.push([ci, gi]); }));
      if (missing.length) series.push({
        type: 'custom', name: '__nodata', silent: true, tooltip: { show: false }, data: missing,
        renderItem: (params, api) => {
          const ci = api.value(0), gi = api.value(1);
          const [x0, y0] = api.coord([ci, 0]);
          const band = api.size([1, 0])[0];
          const x = x0 + (-0.4 + (gi + 0.5) * (0.8 / groups.length)) * band;
          return { type: 'text', x, y: y0 - 4, rotation: Math.PI / 2,
            style: { text: 'no data', fill: tk.muted, font: `${tk.captionPx}px ${tk.family}`, align: 'left', verticalAlign: 'middle' } };
        },
      });
    } else if (layer.type === 'heatmap') {
      const yf = field(enc, 'y'), cf = field(enc, 'color');
      const rs = rows(spec, layer.data);
      categoryX = [...new Set(rs.map((r) => r[xf]))];
      categoryY = [...new Set(rs.map((r) => r[yf]))];
      const vals = rs.map((r) => r[cf]).filter((v) => v != null);
      series.push({ type: 'heatmap', name,
        // null cells are left out entirely: blank, not the colour of 0.
        data: rs.filter((r) => r[cf] != null).map((r) => [categoryX.indexOf(r[xf]), categoryY.indexOf(r[yf]), r[cf]]) });
      const unit = spec.datasets[layer.data].columns[cf].unit;
      visualMap = { type: 'continuous', min: Math.min(...vals), max: Math.max(...vals), calculable: false,
        orient: 'vertical', right: 8, top: 'middle', itemHeight: 260,
        text: [`${fmt(Math.max(...vals))} ${unit ?? cf}`, fmt(Math.min(...vals))],
        inRange: { color: tk.sequential }, textStyle: text, formatter: (v) => fmt(Math.round(v)) };
    } else {
      throw new UnsupportedSpec(`mark ${layer.type} not in spike`);
    }
  });

  const markLines = [];
  for (const a of spec.annotations || []) {
    if (a.type !== 'reference_line' || a.axis !== 'y') throw new UnsupportedSpec(`annotation ${a.type}/${a.axis}`);
    markLines.push({ yAxis: a.value, name: a.label, label: { formatter: a.label ?? '', position: 'insideEndTop', color: tk.muted, fontFamily: tk.family, fontSize: tk.captionPx } });
  }
  if (markLines.length) series.push({ type: 'line', name: '__reference', data: [], silent: true,
    markLine: { symbol: 'none', lineStyle: { color: tk.referenceColor, type: [6, 3, 1, 3], width: 1.2 }, data: markLines } });

  const yLimits = spec.axes?.y?.limits ?? [null, null];
  const axisCommon = { nameLocation: 'middle', nameTextStyle: text, axisLabel: { ...text, hideOverlap: true },
    axisLine: { lineStyle: { color: tk.foreground } }, splitLine: { lineStyle: { color: tk.grid } } };

  return {
    animation: false, // deterministic static output
    useUTC: true, // wall-clock-as-UTC values, see common.wallClockMs
    backgroundColor: tk.background,
    textStyle: { fontFamily: tk.family },
    aria: { enabled: true, label: { description: describe(spec, theme) } },
    title: { text: spec.title, left: 'center', top: 4, textStyle: { ...text, fontSize: tk.fontPx * 1.2, fontWeight: 'normal' } },
    legend: { data: legend, right: 30, top: 34, textStyle: text },
    tooltip: { trigger: categoryY ? 'item' : 'axis' },
    grid: { left: 80, right: visualMap ? 110 : 30, top: 80, bottom: 75 },
    xAxis: categoryX
      ? { ...axisCommon, type: 'category', data: categoryX, name: axisTitle(spec, 'x'), nameGap: 28, splitLine: { show: false } }
      : { ...axisCommon, type: xKind === 'time' ? 'time' : 'value', name: axisTitle(spec, 'x'), nameGap: 28,
        axisLabel: { ...text, hideOverlap: true, formatter: xKind === 'time' ? timeLabelFormatter(theme) : (v) => fmt(v) } },
    yAxis: categoryY
      ? { ...axisCommon, type: 'category', data: categoryY, name: axisTitle(spec, 'y'), nameGap: 40, splitLine: { show: false } }
      : { ...axisCommon, type: 'value', name: axisTitle(spec, 'y'), nameGap: 55, scale: yLimits[0] == null,
        min: yLimits[0] ?? undefined, max: yLimits[1] ?? undefined, axisLabel: { ...text, formatter: (v) => fmt(v) } },
    ...(visualMap ? { visualMap } : {}),
    graphic: spec.caption ? [{ type: 'text', left: 8, bottom: 6,
      style: { text: spec.caption, fill: tk.muted, font: `italic ${tk.captionPx}px ${tk.family}` } }] : [],
    series,
  };
}
