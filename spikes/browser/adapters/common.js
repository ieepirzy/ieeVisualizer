// Engine-independent helpers shared by the spike adapters.
// Spike code: enough to render the four spike specs honestly, not a general
// FigureSpec implementation. Anything not handled throws, so an unsupported
// spec never renders as something it is not.

export class UnsupportedSpec extends Error {}

/** Rows of a snapshot dataset as objects (nulls kept as null). */
export function rows(spec, name) {
  const ds = spec.datasets[name];
  if (!ds || ds.type !== 'snapshot') throw new UnsupportedSpec(`dataset ${name}: only inline snapshots`);
  const cols = Object.keys(ds.columns);
  const n = ds.columns[cols[0]].values.length;
  const out = [];
  for (let i = 0; i < n; i++) {
    const r = {};
    for (const c of cols) r[c] = ds.columns[c].values[i];
    out.push(r);
  }
  return out;
}

export const column = (spec, data, field) => spec.datasets[data].columns[field];
export const field = (enc, ch) => (enc[ch] == null ? null : typeof enc[ch] === 'string' ? enc[ch] : enc[ch].field);

/** "Label (unit)" from the axis, else the first bound column, as RENDERING.md says. */
export function axisTitle(spec, axis) {
  const a = (spec.axes || {})[axis] || {};
  let label = a.label, unit = a.unit;
  for (const l of spec.layers) {
    const enc = l.encoding || {};
    const chans = axis === 'y' ? ['y', 'y_lower', 'y_upper'] : [axis];
    for (const ch of chans) {
      const f = field(enc, ch);
      if (!f) continue;
      const col = column(spec, l.data, f);
      label ??= enc[ch]?.title ?? f;
      unit ??= col.unit;
    }
  }
  return unit ? `${label} (${unit})` : label;
}

export function axisKind(spec, axis) {
  const a = (spec.axes || {})[axis] || {};
  if (a.scale) return a.scale;
  for (const l of spec.layers) {
    const f = field(l.encoding || {}, axis);
    if (!f) continue;
    const dt = column(spec, l.data, f).dtype;
    if (dt === 'datetime') return 'time';
    if (dt === 'str' || dt === 'bool') return 'category';
    return 'linear';
  }
  return 'linear';
}

/** Timezone the spec's dates are shown in (provenance.timezone), else UTC. */
export function displayTimezone(spec) {
  for (const ds of Object.values(spec.datasets)) if (ds.provenance?.timezone) return ds.provenance.timezone;
  return 'UTC';
}

const dtfCache = new Map();
/**
 * Instant (ISO string, with or without offset) -> milliseconds of the same
 * wall-clock reading in `tz`, expressed as if it were UTC. Every engine here
 * formats dates only in the browser's local zone or in UTC; feeding
 * "wall clock as UTC" and running the engine in UTC mode is how a spec's
 * timezone survives whatever zone the viewer's browser is in.
 * Naive ISO strings are already wall-clock readings and are taken as such.
 */
export function wallClockMs(iso, tz) {
  if (iso == null) return null;
  if (!/[zZ]|[+-]\d\d:\d\d$/.test(iso)) return Date.parse(iso + 'Z');
  let f = dtfCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    dtfCache.set(tz, f);
  }
  const p = Object.fromEntries(f.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
}

/** Number formatting from a theme's NumberLocale (THEMES.md); plain JS otherwise. */
export function numberFormatter(theme) {
  const loc = theme?.locale;
  return (v, decimals = null) => {
    if (v == null) return '';
    if (!loc) return String(v);
    const d = decimals ?? (Number.isInteger(v) ? 0 : Math.min(loc.max_decimals ?? 2, 2));
    const [i, frac] = Math.abs(v).toFixed(d).split('.');
    const grouped = i.length >= (loc.group_min_digits ?? 4) ? i.replace(/\B(?=(\d{3})+(?!\d))/g, loc.group) : i;
    return (v < 0 ? loc.minus : '') + grouped + (frac ? loc.decimal + frac : '');
  };
}

/** Legend label: forecast/reference are said in words, not only shown by style. */
export function legendLabel(layer, fallback) {
  const base = layer.name ?? fallback;
  if (layer.type === 'band') {
    if (layer.interval_level == null || !layer.interval_kind)
      throw new UnsupportedSpec('band without interval_level/interval_kind (roadmap: validation error)');
    return base;
  }
  return layer.role === 'forecast' ? `${base} (forecast)` : base;
}

/** Theme tokens the adapters use, with the renderer defaults a theme may leave out. */
export function tokens(theme) {
  const t = theme || {};
  return {
    background: t.background ?? '#ffffff', foreground: t.foreground ?? '#000000',
    muted: t.muted ?? '#555555', grid: t.grid ?? '#e5e5e5',
    palette: t.palette ?? ['#1f77b4', '#ff7f0e', '#2ca02c'],
    sequential: Array.isArray(t.sequential) ? t.sequential : ['#440154', '#21918c', '#fde725'],
    // Theme widths are points (matplotlib); 100 dpi means 100/72 px per point.
    lineWidth: ((t.line_width ?? 1.5) * 100) / 72, family: (t.typography?.family ?? ['DejaVu Sans']).join(', '),
    // matplotlib: 10 pt at 100 dpi; the spikes render at 100 dpi-equivalent pixels.
    fontPx: ((t.typography?.size ?? 10) * 100) / 72,
    captionPx: ((t.typography?.caption ?? 8) * 100) / 72,
    referenceColor: t.roles?.reference_color ?? '#555555',
    // Theme role token since the themes merge (0.85 in report-light);
    // 0.75 is the Matplotlib renderer default when a theme leaves it out.
    forecastOpacity: t.roles?.forecast_line_alpha ?? 0.75,
    bandOpacity: 0.22,
  };
}

/** Indices i where a series value is non-null but both neighbours are null/absent. */
export function isolatedPoints(values) {
  const out = [];
  values.forEach((v, i) => {
    if (v == null) return;
    if ((i === 0 || values[i - 1] == null) && (i === values.length - 1 || values[i + 1] == null)) out.push(i);
  });
  return out;
}

/**
 * Map of series colour by layer name, so color_of can pair a band with its line.
 * Mirrors the matplotlib adapter's pairing rule (render/mpl/marks.py
 * series_color): a forecast layer takes the colour of the one observed layer
 * plotting the same field. This is presentation logic that each engine adapter
 * would otherwise re-implement; see the evaluation's "resolved spec" proposal.
 */
export function assignColors(spec, palette) {
  const byName = {};
  let k = 0;
  const yField = (l) => field(l.encoding || {}, 'y');
  for (const l of spec.layers) {
    if (l.type === 'band' || l.role === 'reference' || l.style?.color_of) continue;
    const key = l.name ?? `layer${k}`;
    if (l.style?.color) { byName[key] = l.style.color; continue; }
    if (l.role === 'forecast') {
      const partners = spec.layers.filter((o) => o !== l && (o.role ?? 'observed') === 'observed'
        && o.type === l.type && yField(o) === yField(l));
      if (partners.length === 1 && byName[partners[0].name] != null) { byName[key] = byName[partners[0].name]; continue; }
    }
    byName[key] = palette[k++ % palette.length];
  }
  return byName;
}

/** Full category x group grid for grouped bars; missing combinations are explicit. */
export function barGrid(spec, layer) {
  const enc = layer.encoding;
  const xf = field(enc, 'x'), yf = field(enc, 'y'), sf = field(enc, 'series');
  const rs = rows(spec, layer.data);
  const cats = [...new Set(rs.map((r) => r[xf]))];
  const groups = sf ? [...new Set(rs.map((r) => r[sf]))] : [layer.name ?? 'value'];
  const cell = new Map(rs.map((r) => [`${r[xf]}\u0000${sf ? r[sf] : groups[0]}`, r[yf]]));
  const grid = groups.map((g) => cats.map((c) => {
    const key = `${c}\u0000${g}`;
    return { category: c, group: g, value: cell.has(key) ? cell.get(key) : null, present: cell.has(key) };
  }));
  return { cats, groups, grid };
}

/**
 * Plain-language description for screen readers, built from the spec (not
 * from the engine's internal data): units stated, missing values counted as
 * missing, dates in the spec's timezone. ECharts' automatic aria-label read
 * epoch milliseconds and "NaN" for the nulls of the line spec (measured).
 */
export function describe(spec, theme) {
  const fmt = numberFormatter(theme);
  const tz = displayTimezone(spec);
  const day = (iso) => new Intl.DateTimeFormat(theme?.locale?.tag ?? 'en-GB', { timeZone: tz, dateStyle: 'medium' }).format(new Date(iso));
  const parts = [spec.title, spec.caption].filter(Boolean).map((t) => t.replace(/\.$/, ''));
  for (const l of spec.layers) {
    const enc = l.encoding || {};
    const rs = rows(spec, l.data);
    const yf = field(enc, 'y') ?? field(enc, 'color') ?? field(enc, 'y_upper');
    const col = spec.datasets[l.data].columns[yf];
    const vals = rs.map((r) => r[yf]);
    const present = vals.filter((v) => v != null);
    const missing = vals.length - present.length;
    const unit = col?.unit ? ` ${col.unit}` : '';
    const xf = field(enc, 'x');
    const xcol = spec.datasets[l.data].columns[xf];
    const span = xcol?.dtype === 'datetime' ? `, ${day(rs[0][xf])} to ${day(rs[rs.length - 1][xf])}` : '';
    const what = l.type === 'band' ? `${Math.round(l.interval_level * 100)} % ${l.interval_kind} interval` : `${l.type}${l.role === 'forecast' ? ', forecast' : ''}`;
    parts.push(`${l.name ?? l.type} (${what}): ${present.length} values${span}` +
      (present.length ? `, from ${fmt(Math.min(...present))} to ${fmt(Math.max(...present))}${unit}` : '') +
      (missing ? `; ${missing} missing (no data)` : ''));
  }
  return parts.join('. ') + '.';
}

/** Escape text for an HTML context (ECharts' HTML tooltip sets innerHTML). */
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** A colour safe to put in a style attribute, or null. */
export const safeColor = (c) => (typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c) ? c : null);

/** strftime subset (%Y %m %d %H %M %S) on a wall-clock-as-UTC instant. */
export function strftimeUTC(ms, pattern) {
  const d = new Date(ms);
  const p2 = (n) => String(n).padStart(2, '0');
  const map = { Y: String(d.getUTCFullYear()), m: p2(d.getUTCMonth() + 1), d: p2(d.getUTCDate()),
    H: p2(d.getUTCHours()), M: p2(d.getUTCMinutes()), S: p2(d.getUTCSeconds()) };
  return pattern.replace(/%([YmdHMS])/g, (_, k) => map[k]);
}

/**
 * Tooltip/readout text for one value, from the same rules as the static
 * render: theme number format, the column's unit, and "no data" for a null
 * (never 0, never "NaN", never "-"). Stand-in for the shared RenderPlan.
 */
export function valueText(v, unit, fmt) {
  if (v == null || v === '-' || (typeof v === 'number' && Number.isNaN(v))) return 'no data';
  return unit ? `${fmt(v)} ${unit}` : fmt(v);
}
