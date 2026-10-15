// vega + vega-lite compile + View, without vega-embed's actions menu / tooltip plugin.
import * as vega from 'vega';
import { compile } from 'vega-lite';
window.v = { vega, compile };
