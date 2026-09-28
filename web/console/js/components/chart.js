// Lightweight SVG line chart for training metrics.
// props: series = [{ name, color, points: [{x, y}] }], yLabel

export default {
  name: "UiChart",
  props: {
    series: { type: Array, default: () => [] },
    height: { type: Number, default: 190 },
  },
  computed: {
    layout() {
      const width = 640;
      const height = this.height;
      const pad = { left: 46, right: 12, top: 14, bottom: 26 };
      const points = this.series.flatMap((s) => s.points);
      if (!points.length) return null;
      const xs = points.map((p) => p.x);
      const ys = points.map((p) => p.y);
      let xMin = Math.min(...xs);
      let xMax = Math.max(...xs);
      let yMin = Math.min(...ys);
      let yMax = Math.max(...ys);
      if (xMax === xMin) { xMax += 1; }
      if (yMax === yMin) { yMax = yMax + Math.max(1e-6, Math.abs(yMax) * 0.1); }
      const spanY = yMax - yMin;
      yMin -= spanY * 0.06;
      yMax += spanY * 0.06;
      const px = (x) => pad.left + ((x - xMin) / (xMax - xMin)) * (width - pad.left - pad.right);
      const py = (y) => height - pad.bottom - ((y - yMin) / (yMax - yMin)) * (height - pad.top - pad.bottom);
      const paths = this.series.map((s) => ({
        ...s,
        d: s.points.map((p, i) => `${i === 0 ? "M" : "L"}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(" "),
      }));
      const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => {
        const value = yMax - t * (yMax - yMin);
        return { y: py(value), label: this.fmt(value) };
      });
      const xTicks = [xMin, (xMin + xMax) / 2, xMax].map((x) => ({ x: px(x), label: this.fmt(x) }));
      return { width, height, pad, paths, ticks, xTicks };
    },
  },
  methods: {
    fmt(value) {
      const abs = Math.abs(value);
      if (abs >= 100000) return value.toExponential(1);
      if (abs >= 1000) return (value / 1000).toFixed(1) + "k";
      if (abs >= 10) return value.toFixed(0);
      if (abs >= 1) return value.toFixed(1);
      return value.toFixed(3);
    },
  },
  template: `
    <div v-if="!layout" class="faint small" style="padding: 34px 0; text-align:center">{{ $t("chart.noPoints") }}</div>
    <svg v-else :viewBox="'0 0 ' + layout.width + ' ' + layout.height" style="width:100%;display:block">
      <g v-for="tick in layout.ticks">
        <line :x1="layout.pad.left" :x2="layout.width - layout.pad.right" :y1="tick.y" :y2="tick.y"
              stroke="#232c39" stroke-width="1"></line>
        <text :x="layout.pad.left - 7" :y="tick.y + 3.5" text-anchor="end" font-size="10.5" fill="#64748b" font-family="ui-monospace,monospace">{{ tick.label }}</text>
      </g>
      <path v-for="s in layout.paths" :d="s.d" fill="none" :stroke="s.color" stroke-width="1.8"
            stroke-linejoin="round" stroke-linecap="round"></path>
      <g v-for="tick in layout.xTicks">
        <text :x="tick.x" :y="layout.height - 8" text-anchor="middle" font-size="10.5" fill="#64748b" font-family="ui-monospace,monospace">{{ tick.label }}</text>
      </g>
      <g v-if="series.length > 1">
        <template v-for="(s, i) in series">
          <rect :x="layout.pad.left + i * 118" y="2" width="10" height="3" rx="1.5" :fill="s.color"></rect>
          <text :x="layout.pad.left + i * 118 + 14" y="8" font-size="10.5" fill="#9fadc0">{{ s.name }}</text>
        </template>
      </g>
    </svg>
  `,
};
