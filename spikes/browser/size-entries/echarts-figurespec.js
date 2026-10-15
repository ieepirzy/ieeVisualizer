// Tree-shaken: only what a FigureSpec 2D adapter needs, both renderers.
import * as echarts from 'echarts/core';
import { LineChart, BarChart, ScatterChart, HeatmapChart, CustomChart } from 'echarts/charts';
import { GridComponent, PolarComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, MarkLineComponent, MarkAreaComponent, MarkPointComponent,
  VisualMapComponent, DatasetComponent, AriaComponent, BrushComponent, ToolboxComponent } from 'echarts/components';
import { SVGRenderer, CanvasRenderer } from 'echarts/renderers';
import { UniversalTransition } from 'echarts/features';
echarts.use([LineChart, BarChart, ScatterChart, HeatmapChart, CustomChart, GridComponent, PolarComponent,
  TooltipComponent, LegendComponent, TitleComponent, DataZoomComponent, MarkLineComponent,
  MarkAreaComponent, MarkPointComponent, VisualMapComponent, DatasetComponent, AriaComponent,
  BrushComponent, ToolboxComponent, SVGRenderer, CanvasRenderer, UniversalTransition]);
window.e = echarts;
