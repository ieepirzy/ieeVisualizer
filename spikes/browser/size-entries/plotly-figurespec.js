// Custom partial bundle: core + the traces a FigureSpec 2D/polar adapter needs.
import Plotly from 'plotly.js/lib/core';
import scatter from 'plotly.js/lib/scatter';
import bar from 'plotly.js/lib/bar';
import heatmap from 'plotly.js/lib/heatmap';
import histogram from 'plotly.js/lib/histogram';
import scatterpolar from 'plotly.js/lib/scatterpolar';
import barpolar from 'plotly.js/lib/barpolar';
import quiver from 'plotly.js/lib/quiver';
Plotly.register([scatter, bar, heatmap, histogram, scatterpolar, barpolar, quiver]);
window.p = Plotly;
