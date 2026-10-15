// Lower bound: a line chart with axes and tooltip, SVG renderer only.
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
echarts.use([LineChart, GridComponent, TooltipComponent, SVGRenderer]);
window.e = echarts;
