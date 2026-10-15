// figureSpecToVegaLite(spec, theme) -> Vega-Lite spec. Same spike scope as
// adapters/echarts.js. Compiles to Vega; renders in Node (SVG) or the browser.
import {
  UnsupportedSpec, rows, field, axisTitle, axisKind, displayTimezone, wallClockMs,
  legendLabel, tokens, isolatedPoints, assignColors, barGrid,
} from './common.js';

export function figureSpecToVegaLite(spec, theme) {
  if ((spec.coordinate_system ?? 'cartesian2d') !== 'cartesian2d') throw new UnsupportedSpec('only cartesian2d in the spike');
  const tk = tokens(theme);
  const tz = displayTimezone(spec);
  const colors = assignColors(spec, tk.palette);
  const xKind = axisKind(spec, 'x');
  const loc = theme?.locale;
  const df = loc?.date_formats;
  const xVal = (v) => (xKind === 'time' ? wallClockMs(v, tz) : v);
  const xTitle = axisTitle(spec, 'x'), yTitle = axisTitle(spec, 'y');
  const yLimits = spec.axes?.y?.limits ?? [null, null];
  const yScale = yLimits[0] != null && yLimits[1] == null ? { domainMin: yLimits[0] } : { zero: false };
  const xEnc = xKind === 'time'
    ? { field: 'x', type: 'temporal', scale: { type: 'utc' }, title: xTitle,
      // Multi-level date labels from the theme's strftime patterns (d3-time-format
      // uses the same directives). A format object was silently dropped
      // ("customFormatTypes" warning), so the level is picked in labelExpr.
      axis: df ? { labelExpr: `utcdate(datum.value) == 1 ? (utcmonth(datum.value) == 0 ? utcFormat(datum.value, '${df[0]}') : utcFormat(datum.value, '${df[1]}')) : utcFormat(datum.value, '${df[2]}')` } : {} }
    : { field: 'x', type: 'quantitative', title: xTitle, scale: { zero: false } };

  const layers = [];
  const legendDomain = [], legendRange = [], legendDash = [];
  const layerColor = (layer) => layer.style?.color ?? colors[layer.style?.color_of] ?? colors[layer.name] ?? tk.palette[0];

  spec.layers.forEach((layer, li) => {
    const enc = layer.encoding || {};
    const xf = field(enc, 'x');
    const forecast = layer.role === 'forecast';
    const name = legendLabel(layer, `layer ${li}`);
    const color = layerColor(layer);
    if (layer.type === 'line') {
      const yf = field(enc, 'y');
      const rs = rows(spec, layer.data);
      const ys = rs.map((r) => r[yf]);
      const values = rs.map((r) => ({ x: xVal(r[xf]), y: r[yf], series: name }));
      legendDomain.push(name); legendRange.push(color);
      legendDash.push(forecast ? [3.7 * tk.lineWidth, 3.2 * tk.lineWidth] : [1, 0]);
      layers.push({ data: { values }, mark: { type: 'line', strokeWidth: tk.lineWidth, opacity: forecast ? tk.forecastOpacity : 1,
        // explicit: break the path at nulls and keep nulls out of the domain
        invalid: 'break-paths-filter-domains' },
      encoding: { x: xEnc, y: { field: 'y', type: 'quantitative', title: yTitle, scale: yScale },
        color: { field: 'series', type: 'nominal', title: null, legend: { symbolType: 'stroke', symbolStrokeWidth: tk.lineWidth } },
        strokeDash: { field: 'series', type: 'nominal', title: null } } });
      const lone = isolatedPoints(ys);
      if (lone.length) layers.push({ data: { values: lone.map((i) => values[i]) }, mark: { type: 'point', filled: true, size: 36 },
        encoding: { x: xEnc, y: { field: 'y', type: 'quantitative' }, color: { field: 'series', type: 'nominal' } } });
    } else if (layer.type === 'band') {
      const lo = field(enc, 'y_lower'), hi = field(enc, 'y_upper');
      const rs = rows(spec, layer.data);
      // A null bound is a gap: both ends null so the area path breaks there.
      const values = rs.map((r) => {
        const ok = r[lo] != null && r[hi] != null;
        return { x: xVal(r[xf]), lo: ok ? r[lo] : null, hi: ok ? r[hi] : null, band: name };
      });
      layers.push({ data: { values }, mark: { type: 'area', opacity: tk.bandOpacity, color, invalid: 'break-paths-filter-domains' },
        encoding: { x: xEnc, y: { field: 'lo', type: 'quantitative', title: yTitle }, y2: { field: 'hi' },
          fill: { field: 'band', type: 'nominal', title: null, scale: { range: [color] }, legend: { symbolOpacity: tk.bandOpacity } } } });
    } else if (layer.type === 'bar') {
      if ((layer.orientation ?? 'vertical') !== 'vertical') throw new UnsupportedSpec('horizontal bars not in spike');
      const { cats, groups, grid } = barGrid(spec, layer);
      const values = grid.flat().map((c) => ({ x: c.category, g: String(c.group), y: c.value }));
      const xb = { field: 'x', type: 'nominal', sort: cats, title: xTitle, axis: { labelAngle: 0 } };
      const xo = { field: 'g', type: 'nominal', sort: groups.map(String) };
      layers.push({ data: { values }, mark: { type: 'bar' },
        encoding: { x: xb, xOffset: xo, y: { field: 'y', type: 'quantitative', title: yTitle },
          color: { field: 'g', type: 'nominal', title: spec.legend?.title ?? null, scale: { range: tk.palette } } } });
      layers.push({ data: { values: values.filter((v) => v.y == null).map((v) => ({ ...v, y0: 0 })) },
        mark: { type: 'text', text: 'no data', angle: 270, align: 'left', baseline: 'middle', dx: 4, color: tk.muted, fontSize: tk.captionPx },
        encoding: { x: xb, xOffset: xo, y: { field: 'y0', type: 'quantitative' } } });
    } else if (layer.type === 'heatmap') {
      const yf = field(enc, 'y'), cf = field(enc, 'color');
      const rs = rows(spec, layer.data);
      const unit = spec.datasets[layer.data].columns[cf].unit;
      const xs = [...new Set(rs.map((r) => r[xf]))], ys = [...new Set(rs.map((r) => r[yf]))];
      layers.push({ data: { values: rs.map((r) => ({ x: r[xf], y: r[yf], v: r[cf] })) },
        mark: { type: 'rect', invalid: 'filter' }, // null cell: no rect, blank
        // Explicit domains: with 'filter' an all-null column (Sunday) otherwise
        // vanished from the axis (measured). y reversed: first category at the bottom.
        encoding: { x: { field: 'x', type: 'ordinal', scale: { domain: xs }, title: xTitle, axis: { labelAngle: 0 } },
          y: { field: 'y', type: 'ordinal', scale: { domain: [...ys].reverse() }, title: yTitle },
          color: { field: 'v', type: 'quantitative', title: unit ?? cf, scale: { range: tk.sequential },
            legend: { gradientLength: 260 } } } });
    } else {
      throw new UnsupportedSpec(`mark ${layer.type} not in spike`);
    }
  });

  for (const a of spec.annotations || []) {
    if (a.type !== 'reference_line' || a.axis !== 'y') throw new UnsupportedSpec(`annotation ${a.type}/${a.axis}`);
    layers.push({ data: { values: [{}] }, mark: { type: 'rule', color: tk.referenceColor, strokeDash: [6, 3, 1, 3], strokeWidth: 1.2 }, encoding: { y: { datum: a.value } } });
    if (a.label) layers.push({ data: { values: [{}] }, mark: { type: 'text', text: a.label, align: 'right', baseline: 'bottom', dy: -2, color: tk.muted, fontSize: tk.captionPx, x: 'width' },
      encoding: { y: { datum: a.value } } });
  }
  // Line series share one colour/dash legend; scales are resolved across layers.
  for (const l of layers) {
    if (l.encoding?.color?.field === 'series') l.encoding.color.scale = { domain: legendDomain, range: legendRange };
    if (l.encoding?.strokeDash) l.encoding.strokeDash.scale = { domain: legendDomain, range: legendDash };
  }

  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    width: 540, height: 340, background: tk.background,
    title: { text: spec.title ?? '', subtitle: spec.caption, subtitleColor: tk.muted, subtitleFontStyle: 'italic', subtitleFontSize: tk.captionPx },
    // Description goes onto the SVG root as aria-label (Vega's ARIA support).
    description: [spec.title, spec.caption].filter(Boolean).join('. '),
    layer: layers,
    config: {
      font: tk.family,
      axis: { labelColor: tk.foreground, titleColor: tk.foreground, gridColor: tk.grid, domainColor: tk.foreground,
        tickColor: tk.foreground, labelFontSize: tk.fontPx, titleFontSize: tk.fontPx, titleFontWeight: 'normal' },
      legend: { labelColor: tk.foreground, labelFontSize: tk.fontPx, orient: 'right', titleColor: tk.foreground, labelLimit: 400 },
      title: { color: tk.foreground, fontSize: tk.fontPx * 1.2, fontWeight: 'normal' },
      view: { stroke: null },
      ...(loc ? { locale: { number: { decimal: loc.decimal, thousands: loc.group, grouping: [3], currency: ['', `${loc.currency_sep}${loc.currency_symbol}`], minus: loc.minus } } } : {}),
    },
  };
}
